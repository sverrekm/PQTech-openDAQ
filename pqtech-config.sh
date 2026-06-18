#!/bin/bash
# =============================================================
#  pqtech-config — Container-konfigurator for PQTech-openDAQ
# =============================================================
#  Køyrer på HOST-maskina (Raspberry Pi), ikkje inne i containeren.
#  Liknar `raspi-config`: ein terminal-meny for å setje opp
#  containeren før (og mellom) køyringar:
#    - IP-adresse (fast, eller auto = finn ledig IP på subnettet)
#    - Nettverksgrensesnitt (parent for macvlan)
#    - Driftsmodus: node (direkte) eller hub
#    - Kor måledata vert lagra (host-katalog)
#    - Ingest-token (node→hub-autentisering)
#    - Web-port og device-indeks
#    - Bruk endringar (byggjer/startar containeren på nytt)
#
#  Innstillingar vert skrivne til:
#    .env                 (CONTAINER_IP, NET_PARENT, DATA_DIR, ...)
#    konfig/modus.json    (driftsmodus — same fil GUI-et brukar)
#
#  Bruk:
#    sudo bash pqtech-config.sh
# =============================================================
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FIL="$REPO_DIR/.env"
COMPOSE_FIL="$REPO_DIR/docker-compose.yml"
KONFIG_DIR="$REPO_DIR/konfig"
MODUS_FIL="$KONFIG_DIR/modus.json"

APP="PQTech-openDAQ konfigurator"

# ---------------------------------------------------------------
#  docker compose-kommando (v2 «docker compose» eller v1 «docker-compose»)
# ---------------------------------------------------------------
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    DC=""
fi

# ---------------------------------------------------------------
#  UI-lag: whiptail viss tilgjengeleg, elles enkel tekst-meny
# ---------------------------------------------------------------
if command -v whiptail >/dev/null 2>&1; then
    HAR_TUI=1
else
    HAR_TUI=0
fi

ui_input() {  # tittel prompt default  -> echo verdi (rc 0=OK, 1=avbrote)
    local tittel="$1" prompt="$2" def="${3:-}"
    if [ "$HAR_TUI" = 1 ]; then
        whiptail --title "$tittel" --inputbox "$prompt" 11 72 "$def" 3>&1 1>&2 2>&3
    else
        local svar
        printf '\n%s\n%s [%s]: ' "$tittel" "$prompt" "$def" >&2
        read -r svar
        [ -z "$svar" ] && svar="$def"
        printf '%s' "$svar"
    fi
}

ui_msg() {  # tittel tekst
    if [ "$HAR_TUI" = 1 ]; then
        whiptail --title "$1" --msgbox "$2" 18 74
    else
        printf '\n=== %s ===\n%s\n' "$1" "$2" >&2
        printf '\n[Trykk Enter for å halde fram]' >&2; read -r _
    fi
}

ui_yesno() {  # tittel tekst  -> rc 0=ja
    if [ "$HAR_TUI" = 1 ]; then
        whiptail --title "$1" --yesno "$2" 12 72
    else
        local svar
        printf '\n%s\n%s [j/N]: ' "$1" "$2" >&2
        read -r svar
        [[ "$svar" =~ ^[jJyY]$ ]]
    fi
}

ui_meny() {  # tittel tekst  tag1 item1 tag2 item2 ...  -> echo vald tag
    local tittel="$1" tekst="$2"; shift 2
    if [ "$HAR_TUI" = 1 ]; then
        whiptail --title "$tittel" --menu "$tekst" 22 76 12 "$@" 3>&1 1>&2 2>&3
    else
        printf '\n=== %s ===\n%s\n\n' "$tittel" "$tekst" >&2
        local i=1 tag item
        local -a tags=()
        while [ $# -gt 0 ]; do
            tag="$1"; item="$2"; shift 2
            tags+=("$tag")
            printf '  %2d) %-10s %s\n' "$i" "$tag" "$item" >&2
            i=$((i+1))
        done
        local val
        printf '\nVal (nummer, blank = avbryt): ' >&2; read -r val
        [ -z "$val" ] && return 1
        if [[ "$val" =~ ^[0-9]+$ ]] && [ "$val" -ge 1 ] && [ "$val" -le "${#tags[@]}" ]; then
            printf '%s' "${tags[$((val-1))]}"
        else
            return 1
        fi
    fi
}

# ---------------------------------------------------------------
#  .env-handtering
# ---------------------------------------------------------------
les_env() {  # KEY [default] -> echo verdi
    local key="$1" def="${2:-}" v=""
    if [ -f "$ENV_FIL" ]; then
        v="$(grep -E "^${key}=" "$ENV_FIL" 2>/dev/null | tail -1 | cut -d= -f2-)"
    fi
    [ -z "$v" ] && v="$def"
    printf '%s' "$v"
}

sett_env() {  # KEY VALUE  (oppdater eller legg til, behald kommentarar)
    local key="$1" val="$2"
    touch "$ENV_FIL"
    if grep -qE "^${key}=" "$ENV_FIL"; then
        # Bytt ut linja (bruk | som skiljeteikn — stiar kan ha /)
        local tmp; tmp="$(mktemp)"
        sed "s|^${key}=.*|${key}=${val}|" "$ENV_FIL" > "$tmp" && mv "$tmp" "$ENV_FIL"
    else
        printf '%s=%s\n' "$key" "$val" >> "$ENV_FIL"
    fi
}

# ---------------------------------------------------------------
#  modus.json (same fil GUI-et brukar — enhet_konfig.py)
# ---------------------------------------------------------------
les_modus() {  # -> direkte | usbip | hub
    local m="direkte"
    if [ -f "$MODUS_FIL" ]; then
        m="$(grep -oE '"modus"[[:space:]]*:[[:space:]]*"[^"]*"' "$MODUS_FIL" 2>/dev/null \
             | head -1 | sed -E 's/.*"modus"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')"
        [ -z "$m" ] && m="direkte"
    fi
    printf '%s' "$m"
}

sett_modus() {  # modus
    mkdir -p "$KONFIG_DIR"
    printf '{\n  "modus": "%s"\n}\n' "$1" > "$MODUS_FIL"
}

# ---------------------------------------------------------------
#  Nettverk: parent-iface og IP
# ---------------------------------------------------------------
auto_parent() {
    ip route show default 2>/dev/null \
        | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1
}

# Finn ein ledig IP på subnettet til ein gjeven base-IP (192.168.1.X).
# Pingar .150–.199 og returnerer den fyrste som ikkje svarar.
finn_ledig_ip() {  # base-ip -> echo ledig ip (eller tom)
    local base="$1"
    local prefiks; prefiks="$(printf '%s' "$base" | cut -d. -f1-3)"
    [ -z "$prefiks" ] && return 1
    local x ip
    for x in $(seq 150 199); do
        ip="${prefiks}.${x}"
        if ! ping -c1 -W1 "$ip" >/dev/null 2>&1; then
            printf '%s' "$ip"
            return 0
        fi
    done
    return 1
}

# =============================================================
#  Menyhandlingar
# =============================================================

handter_nettverk() {
    local ip parent
    ip="$(les_env CONTAINER_IP 192.168.1.161)"
    parent="$(les_env NET_PARENT "$(auto_parent)")"

    local val
    val="$(ui_meny "Nettverk" \
        "IP: ${ip}\nGrensesnitt (parent): ${parent:-auto}\n\nVel handling:" \
        fast   "Sett fast IP (skriv inn)" \
        auto   "Auto — finn ledig IP på subnettet" \
        parent "Endre nettverksgrensesnitt (parent)" \
        attende "Tilbake")" || return

    case "$val" in
        fast)
            local ny
            ny="$(ui_input "Fast IP" "Container-IP på LAN (macvlan):" "$ip")" || return
            if [[ "$ny" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
                sett_env CONTAINER_IP "$ny"
                sett_env OPENDAQ_IP "$ny"
                ui_msg "Nettverk" "Fast IP sett: $ny\n\nBruk «Bruk endringar» for å aktivere (krev recreate)."
            else
                ui_msg "Feil" "Ugyldig IP-adresse: $ny"
            fi
            ;;
        auto)
            local ledig
            ledig="$(finn_ledig_ip "$ip")"
            if [ -n "$ledig" ]; then
                if ui_yesno "Auto-IP" "Fann ledig IP: $ledig\n\nBruk denne som fast container-IP?"; then
                    sett_env CONTAINER_IP "$ledig"
                    sett_env OPENDAQ_IP "$ledig"
                    ui_msg "Nettverk" "Container-IP sett til $ledig.\n\nBruk «Bruk endringar» for å aktivere."
                fi
            else
                ui_msg "Auto-IP" "Fann ingen ledig IP i ${ip%.*}.150–199.\nSett fast IP manuelt i staden."
            fi
            ;;
        parent)
            local nyp
            nyp="$(ui_input "Grensesnitt" "Nettverksgrensesnitt for macvlan (parent):" "${parent:-eth0}")" || return
            [ -n "$nyp" ] && sett_env NET_PARENT "$nyp"
            ui_msg "Nettverk" "Parent-grensesnitt sett: $nyp"
            ;;
    esac
}

handter_modus() {
    local n="$(les_modus)"
    local val
    val="$(ui_meny "Driftsmodus" \
        "Noverande modus: ${n}\n\nVel rolle for denne containeren:" \
        direkte "Node — måler direkte (SIRIUS via USB)" \
        hub     "Hub — aggregerer fleire fjern-nodar" \
        attende "Tilbake")" || return

    case "$val" in
        direkte)
            sett_modus "direkte"
            sett_env NATIVE_SIRIUS "true"
            ui_msg "Modus" "Sett til NODE (direkte).\n\nBruk «Bruk endringar» for å starte i node-modus."
            ;;
        hub)
            sett_modus "hub"
            ui_msg "Modus" "Sett til HUB.\n\nMåledata frå nodane kan no lagrast på hubben (sjå GUI: Settings → Hub-lagring).\n\nBruk «Bruk endringar» for å starte i hub-modus."
            ;;
    esac
}

handter_datalagring() {
    local dir
    dir="$(les_env DATA_DIR ./maalinger)"
    local ny
    ny="$(ui_input "Datalagring" \
        "Host-katalog der måledata + databasar vert lagra.\n(Absolutt sti, t.d. /mnt/ssd/pqtech, eller ./maalinger):" \
        "$dir")" || return
    [ -z "$ny" ] && return
    # Opprett katalogen viss han ikkje finst (relativ = i repo-rota)
    local abs="$ny"
    case "$ny" in
        /*) abs="$ny" ;;
        *)  abs="$REPO_DIR/${ny#./}" ;;
    esac
    if mkdir -p "$abs" 2>/dev/null; then
        sett_env DATA_DIR "$ny"
        ui_msg "Datalagring" "Måledata vert lagra i:\n  $abs\n\nBruk «Bruk endringar» (recreate) for å aktivere."
    else
        ui_msg "Feil" "Kunne ikkje opprette katalog: $abs"
    fi
}

handter_token() {
    local t
    t="$(les_env INGEST_TOKEN "")"
    local val
    val="$(ui_meny "Ingest-token" \
        "Token som nodar brukar for å pushe til hubben.\nStatus: $([ -n "$t" ] && echo 'satt' || echo 'ikkje satt')" \
        skriv    "Skriv inn token" \
        generer  "Generer tilfeldig token" \
        attende  "Tilbake")" || return
    case "$val" in
        skriv)
            local ny
            ny="$(ui_input "Ingest-token" "Delt token (node ↔ hub):" "$t")" || return
            sett_env INGEST_TOKEN "$ny"
            ui_msg "Token" "Token lagra."
            ;;
        generer)
            local ny
            ny="$(head -c 24 /dev/urandom | base64 2>/dev/null | tr -d '/+=' | head -c 32)"
            if [ -n "$ny" ]; then
                sett_env INGEST_TOKEN "$ny"
                ui_msg "Token" "Generert token:\n\n$ny\n\nBruk same token på alle nodane."
            fi
            ;;
    esac
}

handter_avansert() {
    local port idx
    port="$(les_env WEB_PORT 8080)"
    idx="$(les_env OPENDAQ_DEVICE_IDX 0)"
    local val
    val="$(ui_meny "Avansert" "Web-port: ${port}\nDevice-indeks: ${idx}" \
        port    "Web-port" \
        idx     "Device-indeks (unik localId per node)" \
        attende "Tilbake")" || return
    case "$val" in
        port)
            local ny; ny="$(ui_input "Web-port" "Port for web-grensesnittet:" "$port")" || return
            [[ "$ny" =~ ^[0-9]+$ ]] && sett_env WEB_PORT "$ny" \
                && ui_msg "Avansert" "Web-port sett: $ny"
            ;;
        idx)
            local ny; ny="$(ui_input "Device-indeks" "Unik indeks per node (0,1,2,...):" "$idx")" || return
            [[ "$ny" =~ ^[0-9]+$ ]] && sett_env OPENDAQ_DEVICE_IDX "$ny" \
                && ui_msg "Avansert" "Device-indeks sett: $ny"
            ;;
    esac
}

vis_status() {
    local ut=""
    ut+="Repo:        $REPO_DIR\n"
    ut+="Modus:       $(les_modus)\n"
    ut+="IP:          $(les_env CONTAINER_IP 192.168.1.161)\n"
    ut+="Parent:      $(les_env NET_PARENT "$(auto_parent)")\n"
    ut+="Datalagring: $(les_env DATA_DIR ./maalinger)\n"
    ut+="Web-port:    $(les_env WEB_PORT 8080)\n"
    ut+="Token:       $([ -n "$(les_env INGEST_TOKEN '')" ] && echo 'satt' || echo 'ikkje satt')\n"
    ut+="\n--- Container ---\n"
    if command -v docker >/dev/null 2>&1; then
        local rad
        rad="$(docker ps --filter name=pqtech-opendaq --format '{{.Status}}' 2>/dev/null)"
        ut+="pqtech-opendaq: ${rad:-ikkje køyrande}\n"
    else
        ut+="docker ikkje funne på host\n"
    fi
    ui_msg "Status" "$ut"
}

bruk_endringar() {
    if [ -z "$DC" ]; then
        ui_msg "Feil" "Fann ikkje «docker compose». Installer Docker Compose først."
        return
    fi
    local recreate=1
    if ui_yesno "Bruk endringar" \
        "Dette byggjer/startar containeren på nytt med dei nye innstillingane.\n\nNettverks-/IP-/datasti-endringar krev full recreate (down + up).\n\nHalde fram?"; then
        # Sørg for NET_PARENT (som start.sh)
        local parent; parent="$(les_env NET_PARENT "$(auto_parent)")"
        export NET_PARENT="$parent"
        cd "$REPO_DIR" || { ui_msg "Feil" "Kjem ikkje inn i $REPO_DIR"; return; }
        clear
        echo "== docker compose down =="
        $DC down
        echo ""
        echo "== docker compose up -d --build =="
        if $DC up -d --build; then
            ui_msg "Ferdig" "Containeren er starta på nytt.\n\nWeb-GUI: http://$(les_env CONTAINER_IP 192.168.1.161):$(les_env WEB_PORT 8080)"
        else
            ui_msg "Feil" "docker compose feila. Sjå utskrifta i terminalen."
        fi
    fi
}

vis_logg() {
    if [ -z "$DC" ]; then
        ui_msg "Feil" "Fann ikkje «docker compose»."
        return
    fi
    cd "$REPO_DIR" || return
    clear
    echo "== Siste 200 logglinjer (Ctrl+C for å avslutte live-følging) =="
    $DC logs --tail 200 -f
}

# =============================================================
#  Oppstart
# =============================================================

# Tilby å installere whiptail for ein finare meny (berre som root)
if [ "$HAR_TUI" = 0 ] && [ "$(id -u)" = 0 ] && command -v apt-get >/dev/null 2>&1; then
    echo "whiptail manglar — installerer for ein betre meny..."
    apt-get install -y --no-install-recommends whiptail >/dev/null 2>&1 \
        && command -v whiptail >/dev/null 2>&1 && HAR_TUI=1
fi

if [ ! -f "$COMPOSE_FIL" ]; then
    echo "ÅTVARING: fann ikkje $COMPOSE_FIL"
    echo "Køyr skriptet frå PQTech-openDAQ-repoet (der docker-compose.yml ligg)."
fi

while true; do
    val="$(ui_meny "$APP" "Vel kva du vil setje opp:" \
        nettverk "Nettverk — IP og grensesnitt" \
        modus    "Driftsmodus — node eller hub" \
        lagring  "Datalagring — kor måledata vert lagra" \
        token    "Ingest-token — node↔hub-autentisering" \
        avansert "Avansert — web-port, device-indeks" \
        status   "Vis status" \
        bruk     "Bruk endringar (start container på nytt)" \
        logg     "Vis container-logg" \
        avslutt  "Avslutt")" || break

    case "$val" in
        nettverk) handter_nettverk ;;
        modus)    handter_modus ;;
        lagring)  handter_datalagring ;;
        token)    handter_token ;;
        avansert) handter_avansert ;;
        status)   vis_status ;;
        bruk)     bruk_endringar ;;
        logg)     vis_logg ;;
        avslutt|"") break ;;
    esac
done

clear
echo "Ferdig. Innstillingar lagra i $ENV_FIL og $MODUS_FIL."
