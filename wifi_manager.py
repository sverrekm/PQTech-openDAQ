#!/usr/bin/env python3
"""
WiFi-manager — konfigurer trådlaust nett på verten (Raspberry Pi) frå GUI
=========================================================================
Web-UI-et køyrer inne i containeren, men WiFi må setjast opp på HOST-en
(Pi-en). Containeren er `privileged` med `pid: host` og `NET_ADMIN`, så vi
gjer det same som NAS-modulen: køyrer host-kommandoar i vert-namespacet via
`nsenter -t 1`. Her styrer vi `nmcli` (NetworkManager), som er standard
nettverksstyrar på Raspberry Pi OS Bookworm.

NetworkManager lagrar sjølv WiFi-profilen på verten
(/etc/NetworkManager/system-connections), så tilkoplinga overlever både
reboot og container-restart — vi treng inga eiga konfig- eller cred-fil.
Passordet vert sendt rett til `nmcli` og aldri lagra eller returnert av oss.

Krev at verten har NetworkManager + nmcli (Bookworm har det som standard).
Eldre Raspberry Pi OS (dhcpcd/wpa_supplicant) vert ikkje støtta her — då
rapporterer vi det tydeleg i status().
"""

import re
import logging
import subprocess
import threading
import time

log = logging.getLogger("wifi_manager")

# Tilstand for den siste (asynkrone) tilkoplinga. WiFi-kortet pollar
# /api/wifi/status og les denne, so brukaren ser kva som skjer.
_op_lock = threading.Lock()
_siste_op = {"tilstand": "", "ssid": "", "melding": "", "alder_s": None}
_op_tid = 0.0


def _sett_op(tilstand: str, ssid: str, melding: str = "") -> None:
    global _op_tid
    with _op_lock:
        _siste_op["tilstand"] = tilstand      # koeyrer | ok | feil
        _siste_op["ssid"] = ssid
        _siste_op["melding"] = melding
        _op_tid = time.time()


def _hent_op() -> dict:
    with _op_lock:
        d = dict(_siste_op)
    d["alder_s"] = round(time.time() - _op_tid, 1) if _op_tid else None
    return d

# Køyr i host sitt mount/uts/net/ipc-namespace (same mønster som nas_manager).
_HOST_NS = ["nsenter", "-t", "1", "-m", "-u", "-n", "-i"]

# Split ei nmcli terse-linje (-t) på kolon som IKKJE er escapa med backslash.
_USESC_KOLON = re.compile(r"(?<!\\):")


# ---------------------------------------------------------------
#  Køyr nmcli på verten
# ---------------------------------------------------------------
def _host(cmd: list, timeout: float = 25.0) -> subprocess.CompletedProcess:
    """Køyr ei kommando i host-namespacet. Fell tilbake til direkte kall
    (nyttig i utvikling utan nsenter)."""
    try:
        return subprocess.run(_HOST_NS + cmd, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _nmcli(args: list, timeout: float = 25.0) -> subprocess.CompletedProcess:
    return _host(["nmcli"] + args, timeout=timeout)


def _har_nmcli() -> bool:
    try:
        return _nmcli(["--version"], timeout=8).returncode == 0
    except Exception:
        return False


def _unescape(s: str) -> str:
    return s.replace("\\:", ":").replace("\\\\", "\\")


def _felt(line: str) -> list:
    """Del ei terse-linje i felt og fjern escaping."""
    return [_unescape(x) for x in _USESC_KOLON.split(line)]


def _wifi_dev() -> str:
    """Finn namnet på WiFi-grensesnittet (typisk wlan0)."""
    try:
        r = _nmcli(["-t", "-f", "DEVICE,TYPE", "device", "status"], timeout=10)
        for ln in r.stdout.splitlines():
            f = _felt(ln)
            if len(f) >= 2 and f[1] == "wifi":
                return f[0]
    except Exception:
        pass
    return "wlan0"


# ---------------------------------------------------------------
#  Radio + status
# ---------------------------------------------------------------
def _radio_på() -> None:
    try:
        r = _nmcli(["radio", "wifi"], timeout=8)
        if "enabled" not in (r.stdout or "").lower():
            _nmcli(["radio", "wifi", "on"], timeout=10)
    except Exception:
        pass


def status() -> dict:
    """Noverande WiFi-tilstand. Ingen hemmelegheiter."""
    ut = {
        "nmcli_tilgjengeleg": False,
        "radio": None,          # True/False/None
        "device": "",
        "tilkobla": False,
        "ssid": "",
        "signal": None,         # 0-100
        "ip": "",
        "tilstand": "",
        "siste_op": _hent_op(),
    }
    if not _har_nmcli():
        ut["feil"] = ("NetworkManager (nmcli) ikkje funne på verten. "
                      "Krev Raspberry Pi OS Bookworm eller nyare.")
        return ut
    ut["nmcli_tilgjengeleg"] = True

    try:
        r = _nmcli(["radio", "wifi"], timeout=8)
        ut["radio"] = "enabled" in (r.stdout or "").lower()
    except Exception:
        pass

    dev = _wifi_dev()
    ut["device"] = dev
    try:
        r = _nmcli(["-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS",
                    "device", "show", dev], timeout=12)
        for ln in r.stdout.splitlines():
            k, _, v = ln.partition(":")
            v = v.strip()
            if k == "GENERAL.STATE":
                ut["tilstand"] = v
                ut["tilkobla"] = v.startswith("100")   # 100 (connected)
            elif k == "GENERAL.CONNECTION" and v and v != "--":
                # Dette er PROFILNAMNET, ikkje SSID-en. Raspberry Pi Imager
                # kallar profilen sin "preconfigured", og finst profilen frå
                # før lagar nmcli "SSID 1". Vi viste dette som SSID før, noko
                # som gjorde at nodane såg ut til å stå på eit nett som heitte
                # «preconfigured». Rett SSID vert henta under.
                ut["profil"] = v
                ut["ssid"] = v
            elif k.startswith("IP4.ADDRESS") and v and v != "--":
                ut["ip"] = v.split("/")[0]
    except Exception as e:
        ut["feil"] = str(e)

    # Signalstyrke for det aktive nettet (IN-USE = *)
    if ut["tilkobla"]:
        try:
            r = _nmcli(["-t", "-f", "IN-USE,SIGNAL,SSID", "device", "wifi", "list"],
                       timeout=12)
            for ln in r.stdout.splitlines():
                f = _felt(ln)
                if len(f) >= 3 and f[0].strip() == "*":
                    try:
                        ut["signal"] = int(f[1])
                    except ValueError:
                        pass
                    ekte_ssid = f[2].strip()
                    if ekte_ssid:
                        ut["ssid"] = ekte_ssid
                    break
        except Exception:
            pass
    return ut


# ---------------------------------------------------------------
#  Skanning
# ---------------------------------------------------------------
def skann() -> dict:
    """Skann etter tilgjengelege nett. Returnerer {suksess, nett: [...]}."""
    if not _har_nmcli():
        return {"suksess": False,
                "melding": "NetworkManager (nmcli) ikkje tilgjengeleg på verten."}
    _radio_på()
    try:
        r = _nmcli(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                    "device", "wifi", "list", "--rescan", "yes"], timeout=30)
    except Exception as e:
        return {"suksess": False, "melding": f"Skann feila: {e}"}
    if r.returncode != 0:
        return {"suksess": False,
                "melding": (r.stderr or r.stdout or "Skann feila").strip()}

    beste = {}   # ssid -> nett (behald sterkaste signal)
    for ln in r.stdout.splitlines():
        f = _felt(ln)
        if len(f) < 4:
            continue
        in_use, ssid, signal, sec = f[0].strip(), f[1], f[2], f[3].strip()
        if not ssid:
            continue    # skjulte nett har tomt SSID
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        n = {"ssid": ssid, "signal": sig,
             "sikring": sec if sec and sec != "--" else "",
             "open": (not sec or sec == "--"),
             "aktiv": in_use == "*"}
        if ssid not in beste or sig > beste[ssid]["signal"]:
            beste[ssid] = n
    nett = sorted(beste.values(), key=lambda x: x["signal"], reverse=True)
    return {"suksess": True, "nett": nett}


# ---------------------------------------------------------------
#  Kople til / gløym
# ---------------------------------------------------------------
_NM_TILSTAND = {
    "20": "grensesnittet er utilgjengeleg",
    "30": "fråkopla",
    "40": "leitar etter nettet",
    "50": "held på å autentisere",
    "60": "ventar på autentisering (passord?)",
    "70": ("assosiert med nettet, men fekk ikkje IP-adresse — DHCP-serveren "
           "svarar ikkje. Prøv statisk IP."),
    "100": "tilkopla",
}


def _dev_tilstand(dev: str) -> tuple:
    """(kode, rå-tekst) frå GENERAL.STATE for grensesnittet."""
    try:
        r = _nmcli(["-t", "-f", "GENERAL.STATE", "device", "show", dev],
                   timeout=10)
        for ln in r.stdout.splitlines():
            k, _, v = ln.partition(":")
            if k == "GENERAL.STATE":
                v = v.strip()
                return v.split(" ")[0], v
    except Exception:
        pass
    return "", ""


def _forklar_tilstand(dev: str) -> str:
    """Menneskeleg forklaring på kvar tilkoplinga står.

    Ein rå `subprocess timed out`-streng seier ingenting om kva som gjekk
    gale. NetworkManager veit det: state 70 tyder at radioen er inne, men
    DHCP ikkje svarar — heilt annan feil enn 60 (passord).
    """
    kode, raa = _dev_tilstand(dev)
    if not kode:
        return ""
    forklaring = _NM_TILSTAND.get(kode, "")
    return f"{forklaring} (NM-tilstand {raa})" if forklaring else f"NM-tilstand {raa}"


def _nett_i_bruk(unnta_dev: str = "") -> dict:
    """{nettverk: grensesnitt} for alle IPv4-adresser på verten.

    Brukt til å fange subnettkollisjonar før dei skjer. Eit instrument med
    innebygd ruter kjem typisk med 192.168.1.0/24 rett frå fabrikken — same
    subnett som mange kunde-LAN. Legg ein då wlan0 på same nett som eth0,
    blir rutinga tvitydig og kabelvegen kan ryke. (Vi har alt sett kva to
    kundenett på 192.168.1.0/24 gjer.)
    """
    ut = {}
    try:
        import ipaddress
        r = _host(["ip", "-o", "-f", "inet", "addr", "show"], timeout=10)
        for ln in r.stdout.splitlines():
            f = ln.split()
            if len(f) < 4:
                continue
            dev, cidr = f[1], f[3]
            if dev == "lo" or dev == unnta_dev:
                continue
            try:
                ut[str(ipaddress.ip_interface(cidr).network)] = dev
            except Exception:
                continue
    except Exception:
        pass
    return ut


def _kollisjon(cidr: str, unnta_dev: str = "") -> str:
    """Tom streng om `cidr` er trygg, elles ei forklaring."""
    try:
        import ipaddress
        nett = str(ipaddress.ip_interface(cidr).network)
    except Exception:
        return ""
    treff = _nett_i_bruk(unnta_dev).get(nett)
    if treff:
        return (f"Subnettet {nett} er alt i bruk på {treff}. To grensesnitt "
                f"på same subnett gir tvitydig ruting, og kabelvegen inn til "
                f"noden kan ryke. Endre subnettet på instrument-ruteren "
                f"(t.d. til 192.168.50.0/24) før du koplar til.")
    return ""


def _rydd_opp(dev: str) -> None:
    """Kople frå eit forsøk som står fast, så wlan0 ikkje blir liggjande
    og prøve i det uendelege."""
    try:
        _nmcli(["device", "disconnect", dev], timeout=15)
    except Exception:
        pass


def _aktiv_profil(dev: str) -> str:
    """Namnet NetworkManager faktisk gav profilen på dette grensesnittet.

    Vi kan ikkje gjette at profilen heiter det same som SSID-en: Raspberry
    Pi Imager lagar «preconfigured», og finst profilen frå før lagar nmcli
    «SSID 1». `connection modify` mot feil namn feilar stille.
    """
    try:
        r = _nmcli(["-t", "-f", "GENERAL.CONNECTION", "device", "show", dev],
                   timeout=10)
        for ln in r.stdout.splitlines():
            k, _, v = ln.partition(":")
            if k == "GENERAL.CONNECTION" and v.strip() and v.strip() != "--":
                return v.strip()
    except Exception:
        pass
    return ""


def _profil_for_ssid(ssid: str) -> str:
    """Finn profilnamnet som høyrer til eit SSID. Fell tilbake til SSID-en."""
    try:
        r = _nmcli(["-t", "-f", "NAME,TYPE", "connection", "show"], timeout=12)
        namn = []
        for ln in r.stdout.splitlines():
            f = _felt(ln)
            if len(f) >= 2 and "wireless" in f[1]:
                namn.append(_unescape(f[0]))
        if ssid in namn:
            return ssid
        for n in namn:
            r2 = _nmcli(["-t", "-f", "802-11-wireless.ssid", "connection",
                         "show", n], timeout=10)
            for ln in r2.stdout.splitlines():
                _, _, v = ln.partition(":")
                if v.strip() == ssid:
                    return n
    except Exception:
        pass
    return ssid


def _sett_berre_lokalt(ssid: str, dev: str) -> str:
    """Gjer eit WiFi-nett til reint instrumentnett.

    Eit måleinstrument med innebygd ruter (Elspec BlackBox) deler ut både
    gateway og DNS over DHCP. Tek den over default-ruta, mistar noden
    internett og Tailscale — altså deg. Difor: la profilen rute berre sitt
    eige subnett, og aldri vere veg ut.

    Motsett veg (wifi mot ein 5G-ruter som ER internettvegen) skal IKKJE
    ha dette, og då kallar vi ikkje denne.
    """
    profil = _aktiv_profil(dev) or _profil_for_ssid(ssid)
    r = _nmcli(["connection", "modify", profil,
                "ipv4.never-default", "yes",
                "ipv4.ignore-auto-dns", "yes",
                "ipv6.never-default", "yes"], timeout=20)
    if r.returncode != 0:
        return (r.stderr or r.stdout or "").strip()
    # Profilendringa slår ikkje inn før tilkoplinga er reaktivert.
    _nmcli(["connection", "up", profil, "ifname", dev], timeout=45)
    return ""


def koble_til(ssid: str, passord: str = "", skjult: bool = False,
              berre_lokalt: bool = False, statisk_ip: str = "",
              gateway: str = "") -> tuple:
    """Start tilkopling til eit WiFi-nett. Returnerer med ein gong.

    Tilkoplinga køyrer i bakgrunnen fordi ho er treg: `nmcli device wifi
    connect` kan bruke 45 s, og med `berre_lokalt` kjem ein reaktivering
    på toppen. Hub-proxyen gir opp etter 30 s lesetimeout, så eit synkront
    kall gav 502 sjølv når tilkoplinga gjekk fint. WiFi-kortet pollar
    /api/wifi/status og les `siste_op` for å sjå korleis det gjekk.

    `berre_lokalt=True` for instrumentnett (Elspec BlackBox o.l.): nettet
    blir nåbart, men får aldri vere default-rute eller DNS-kjelde. Bruk
    False når wifi-et ER vegen ut (t.d. 5G-ruter).

    Passordet vert aldri logga.
    """
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "Manglar SSID"
    if not _har_nmcli():
        return False, "NetworkManager (nmcli) ikkje tilgjengeleg på verten."

    with _op_lock:
        if _siste_op.get("tilstand") == "koeyrer":
            return False, (f"Ei tilkopling til «{_siste_op.get('ssid')}» "
                           f"pågår alt — vent til ho er ferdig.")

    _sett_op("koeyrer", ssid, "Koplar til …")
    threading.Thread(
        target=_koble_synk,
        args=(ssid, passord, skjult, berre_lokalt, statisk_ip, gateway),
        daemon=True, name="wifi-koble").start()
    return True, f"Koplar til «{ssid}» … følg med på statusen."


def _koble_synk(ssid: str, passord: str, skjult: bool, berre_lokalt: bool,
                statisk_ip: str = "", gateway: str = "") -> None:
    """Sjølve tilkoplinga. Køyrer i bakgrunnstråd; melder frå via _sett_op."""
    try:
        ok, melding = _koble_no(ssid, passord, skjult, berre_lokalt,
                                statisk_ip, gateway)
    except Exception as e:
        _sett_op("feil", ssid, str(e))
        return
    _sett_op("ok" if ok else "feil", ssid, melding)


def _koble_statisk(ssid: str, passord: str, skjult: bool, dev: str,
                   statisk_ip: str, gateway: str) -> tuple:
    """Lag profilen med fast IP og aktiver han.

    Går utanom `device wifi connect`, som ventar på DHCP. Eit instrument
    med innebygd ruter deler ikkje alltid ut leige i det heile — då står
    NetworkManager for evig i tilstand 70 (getting IP configuration).
    """
    profil = _profil_for_ssid(ssid)
    _nmcli(["connection", "delete", "id", profil], timeout=15)

    cmd = ["connection", "add", "type", "wifi", "ifname", dev,
           "con-name", ssid, "ssid", ssid,
           "ipv4.method", "manual", "ipv4.addresses", statisk_ip]
    if gateway:
        cmd += ["ipv4.gateway", gateway]
    if passord:
        cmd += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", passord]
    if skjult:
        cmd += ["802-11-wireless.hidden", "yes"]

    r = _nmcli(cmd, timeout=30)
    if r.returncode != 0:
        feil = (r.stderr or r.stdout or "").strip()
        if passord:
            feil = feil.replace(passord, "***")
        return False, f"Kunne ikkje lage profilen: {feil}"

    try:
        r = _nmcli(["connection", "up", ssid, "ifname", dev], timeout=60)
    except Exception as e:
        _rydd_opp(dev)
        return False, f"Aktivering feila: {e}. {_forklar_tilstand(dev)}"
    if r.returncode != 0:
        feil = (r.stderr or r.stdout or "").strip()
        _rydd_opp(dev)
        return False, f"{feil or 'Aktivering feila'}. {_forklar_tilstand(dev)}"
    return True, f"Kopla til «{ssid}» med fast IP {statisk_ip}"


def _koble_no(ssid: str, passord: str = "", skjult: bool = False,
              berre_lokalt: bool = False, statisk_ip: str = "",
              gateway: str = "") -> tuple:
    """Blokkerande tilkopling. Returnerer (ok, melding)."""
    _radio_på()
    dev = _wifi_dev()

    if statisk_ip:
        kol = _kollisjon(statisk_ip, unnta_dev=dev)
        if kol:
            return False, kol
        ok, melding = _koble_statisk(ssid, passord, skjult, dev,
                                     statisk_ip, gateway)
        if ok and berre_lokalt:
            feil = _sett_berre_lokalt(ssid, dev)
            if feil:
                return True, f"{melding}, men låsinga til instrumentnett feila: {feil}"
            return True, f"{melding} (ingen default-rute eller DNS herifrå)"
        return ok, melding

    cmd = ["device", "wifi", "connect", ssid]
    if passord:
        cmd += ["password", passord]
    if skjult:
        cmd += ["hidden", "yes"]
    cmd += ["ifname", dev]

    try:
        # NetworkManager sin eigen timeout er 90 s. Vi låg under han med 45,
        # so vi drap kommandoen midt i og rapporterte «timed out» i staden
        # for kva som faktisk stod på.
        r = _nmcli(cmd, timeout=100)
    except Exception:
        forklaring = _forklar_tilstand(dev)
        _rydd_opp(dev)
        return False, (f"Tilkoplinga vart ikkje ferdig. {forklaring}"
                       if forklaring else "Tilkoplinga vart ikkje ferdig.")
    if r.returncode == 0:
        log.info(f"WiFi kopla til SSID={ssid!r} på {dev} "
                 f"(berre_lokalt={berre_lokalt})")
        if berre_lokalt:
            feil = _sett_berre_lokalt(ssid, dev)
            if feil:
                return True, (f"Kopla til «{ssid}», men kunne ikkje låse han "
                              f"til instrumentnett: {feil}")
            return True, (f"Kopla til «{ssid}» som instrumentnett "
                          f"(ingen default-rute eller DNS herifrå)")
        adr = status().get("ip") or ""
        if adr:
            kol = _kollisjon(f"{adr}/24", unnta_dev=dev)
            if kol:
                return True, f"Kopla til «{ssid}» ({adr}) — MEN: {kol}"
        return True, f"Kopla til «{ssid}»"
    feil = (r.stderr or r.stdout or "").strip()
    # Ikkje lek passord om nmcli skulle ekko kommandoen
    if passord:
        feil = feil.replace(passord, "***")
    forklaring = _forklar_tilstand(dev)
    _rydd_opp(dev)
    return False, " ".join(x for x in (feil or "Tilkopling feila", forklaring) if x)


def gløym(ssid: str) -> tuple:
    """Slett den lagra profilen for eit nett."""
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "Manglar SSID"
    if not _har_nmcli():
        return False, "NetworkManager (nmcli) ikkje tilgjengeleg på verten."
    # Profilen heiter ikkje nødvendigvis det same som SSID-en.
    profil = _profil_for_ssid(ssid)
    try:
        r = _nmcli(["connection", "delete", "id", profil], timeout=15)
    except Exception as e:
        return False, str(e)
    if r.returncode == 0:
        return True, f"Gløymde «{ssid}»"
    return False, (r.stderr or r.stdout or "Kunne ikkje gløyme nettet").strip()
