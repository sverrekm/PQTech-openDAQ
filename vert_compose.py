#!/usr/bin/env python3
"""
Vert-compose — bygg om containeren frå web-GUI-et
=================================================
Nokre endringar naar ikkje fram med fleet-oppdateringa, som berre kopierer
`*.py`, entrypoint og `frontend/dist` inn i den koeyrande containeren. Nye
DOCKER-NETTVERK er slike: dei staar i `docker-compose.yml` og krev at
containeren blir bygd om. Fram til no har det tydd at nokon maatte reise
til verten og koeyre `docker compose up -d`.

Det gjer vi herifrae i staden. Men det er ein operasjon som drep prosessen
som utfoerer han, so han maa gjerast varsamt:

  * Repo-katalogen paa VERTEN finn vi ved aa lese `/proc/self/mountinfo`.
    Bind-mountet `./konfig:/data/konfig` fortel kva host-sti /data/konfig
    kjem frae; foreldrekatalogen er repoet. Ingenting maa hardkodast.
  * `.env` blir sjekka FOERST. Startar containeren med `NET_PARENT=eth0` paa
    ein node der grensesnittet heiter `end0`, kjem han aldri opp igjen - og
    da er noden borte til nokon reiser dit. Vi fyller inn manglande verdiar
    frae den faktiske tilstanden foer vi roerer noko.
  * `docker-compose.yml` paa verten er ikkje oppdatert av fleet-oppdateringa,
    so vi kopierer inn den nye (med backup) foer vi byggjer om.
  * Sjoelve `docker compose up -d` blir koeyrt LAUSRIVE frae oss (setsid), so
    han fullfoerer sjoelv om containeren vaar blir riven ned midt i. Utdata
    hamnar paa det persistente volumet, so vi kan lese resultatet naar vi
    kjem opp att.

Manglar docker-CLI paa verten (nokre nodar koeyrer under containerd utan
den), seier vi det tydeleg i staden for aa proeve og feile.
"""

import json
import os
import re
import subprocess
import time

_HOST_NS = ["nsenter", "-t", "1", "-m", "-u", "-n", "-i"]

UT_FIL = "/data/konfig/compose_ut.txt"


def _host(cmd: list, timeout: float = 30.0):
    try:
        return subprocess.run(_HOST_NS + cmd, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception as e:
        class R:
            returncode, stdout, stderr = 1, "", str(e)
        return R()


def _ok(r) -> bool:
    return r is not None and getattr(r, "returncode", 1) == 0


# ---------------------------------------------------------------
#  Finn repoet paa verten
# ---------------------------------------------------------------
def finn_repo() -> str:
    """Host-stien til compose-prosjektet, utleidd frå bind-mountet.

    `./konfig:/data/konfig` i compose tyder at kjelda til /data/konfig ligg
    i repo-katalogen. mountinfo gir oss kjeldestien; foreldrekatalogen er
    repoet.
    """
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as f:
            for ln in f:
                felt = ln.split()
                if len(felt) < 5:
                    continue
                if felt[4] == "/data/konfig":
                    kjelde = felt[3]
                    if kjelde and kjelde != "/":
                        return os.path.dirname(kjelde.rstrip("/"))
    except Exception:
        pass
    return ""


def _har_docker() -> tuple:
    r = _host(["sh", "-c", "command -v docker >/dev/null 2>&1 && "
                           "docker compose version 2>&1 | head -1"])
    ut = (r.stdout or "").strip()
    if _ok(r) and ut:
        return True, ut
    return False, "docker compose not found on the host"


# ---------------------------------------------------------------
#  Grensesnitt (til nedtrekksmenyar i GUI-et)
# ---------------------------------------------------------------
def grensesnitt() -> list:
    """Nettverksgrensesnitta på VERTEN, med adresse når dei har ei.

    Brukt til nedtrekksmenyar: namna varierer mellom nodane (`eth0` på
    nokre, `end0` på andre), og å skrive dei for hand er ei feilkjelde vi
    ikkje treng.
    """
    ut = {}
    r = _host(["ip", "-o", "link", "show"])
    if _ok(r):
        for ln in r.stdout.splitlines():
            m = re.match(r"\d+:\s+([^:@]+)[:@]", ln)
            if not m:
                continue
            dev = m.group(1).strip()
            if dev == "lo" or dev.startswith(("docker", "br-", "veth")):
                continue
            ut[dev] = {"dev": dev, "adresse": "", "oppe": "state UP" in ln,
                       "type": "wifi" if dev.startswith(("wlan", "wlp"))
                               else "kabel"}
    r = _host(["ip", "-o", "-f", "inet", "addr", "show"])
    if _ok(r):
        for ln in r.stdout.splitlines():
            f = ln.split()
            if len(f) >= 4 and f[1] in ut:
                ut[f[1]]["adresse"] = f[3]
    return sorted(ut.values(), key=lambda d: (d["type"] != "kabel", d["dev"]))


# ---------------------------------------------------------------
#  .env
# ---------------------------------------------------------------
def _les_env(repo: str) -> dict:
    r = _host(["sh", "-c", f"cat '{repo}/.env' 2>/dev/null"])
    d = {}
    for ln in (r.stdout or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        d[k.strip()] = v.strip()
    return d


def _standard_dev() -> str:
    r = _host(["ip", "route", "show", "default"])
    if _ok(r):
        for ln in r.stdout.splitlines():
            f = ln.split()
            if "dev" in f:
                return f[f.index("dev") + 1]
    return ""


def _subnett_gw(dev: str) -> tuple:
    sn = gw = ""
    r = _host(["ip", "-o", "-f", "inet", "addr", "show", "dev", dev])
    if _ok(r):
        for ln in r.stdout.splitlines():
            f = ln.split()
            if len(f) >= 4:
                try:
                    import ipaddress
                    sn = str(ipaddress.ip_interface(f[3]).network)
                except Exception:
                    pass
                break
    r = _host(["ip", "route", "show", "default"])
    if _ok(r):
        for ln in r.stdout.splitlines():
            f = ln.split()
            if "dev" in f and f[f.index("dev") + 1] == dev and "via" in f:
                gw = f[f.index("via") + 1]
                break
    return sn, gw


def sjekk_env(repo: str) -> dict:
    """Kva .env manglar for at ein recreate skal vere trygg.

    Dette er den farlege delen: startar containeren med feil NET_PARENT,
    finst ikkje macvlan-foreldra, og han kjem aldri opp igjen.
    """
    env = _les_env(repo)
    dev = env.get("NET_PARENT") or _standard_dev()
    sn, gw = _subnett_gw(dev) if dev else ("", "")
    manglar = {}
    if not env.get("NET_PARENT") and dev:
        manglar["NET_PARENT"] = dev
    if not env.get("NET_SUBNET") and sn:
        manglar["NET_SUBNET"] = sn
    if not env.get("NET_GATEWAY") and gw:
        manglar["NET_GATEWAY"] = gw
    if not env.get("CONTAINER_IP"):
        ip = (os.environ.get("OPENDAQ_IP") or "").strip()
        if ip:
            manglar["CONTAINER_IP"] = ip

    trygt, grunn = True, ""
    faktisk_dev = env.get("NET_PARENT") or manglar.get("NET_PARENT", "")
    if not faktisk_dev:
        trygt, grunn = False, ("Could not determine which interface the "
                               "container should attach to (NET_PARENT).")
    return {"env": env, "manglar": manglar, "trygt": trygt, "grunn": grunn,
            "grensesnitt": faktisk_dev}


def _skriv_env(repo: str, nye: dict) -> str:
    for k, v in nye.items():
        r = _host(["sh", "-c",
                   f"touch '{repo}/.env' && "
                   f"sed -i '/^{k}=/d' '{repo}/.env' && "
                   f"printf '%s=%s\\n' '{k}' '{v}' >> '{repo}/.env'"])
        if not _ok(r):
            return (r.stderr or r.stdout or f"could not write {k}").strip()
    return ""


# ---------------------------------------------------------------
#  Compose-fila
# ---------------------------------------------------------------
def _compose_har(repo: str, naal: str) -> bool:
    r = _host(["sh", "-c",
               f"grep -q '{naal}' '{repo}/docker-compose.yml' && echo ja"])
    return _ok(r) and "ja" in (r.stdout or "")


def oppdater_compose_fil(repo: str) -> str:
    """Kopier /app/docker-compose.yml til verten, med backup."""
    kjelde = "/app/docker-compose.yml"
    if not os.path.exists(kjelde):
        return ("docker-compose.yml is not in /app - run a normal update "
                "first.")
    try:
        with open(kjelde, "r", encoding="utf-8") as f:
            innhald = f.read()
    except Exception as e:
        return str(e)
    stempel = time.strftime("%Y%m%d-%H%M%S")
    r = _host(["sh", "-c",
               f"cp -p '{repo}/docker-compose.yml' "
               f"'{repo}/docker-compose.yml.bak-{stempel}' 2>/dev/null; true"])
    # Skriv via stdin so vi slepp shell-siteringsproblem med YAML-innhald
    try:
        p = subprocess.run(_HOST_NS + ["sh", "-c",
                                       f"cat > '{repo}/docker-compose.yml'"],
                           input=innhald, capture_output=True, text=True,
                           timeout=30)
    except Exception as e:
        return str(e)
    if p.returncode != 0:
        return (p.stderr or p.stdout or "could not write compose file").strip()
    return ""


# ---------------------------------------------------------------
#  Status + bygg om
# ---------------------------------------------------------------
def status() -> dict:
    repo = finn_repo()
    har, ver = _har_docker()
    ut = {
        "repo": repo,
        "docker": har,
        "docker_versjon": ver,
        "compose_har_instrumentnett": False,
        "env": {}, "manglar": {}, "trygt": False, "grunn": "",
        "grensesnitt": "",
        "siste_utdata": "",
    }
    if not repo:
        ut["grunn"] = ("Could not locate the compose project on the host. "
                       "/data/konfig is not a bind mount from the repo.")
        return ut
    if not har:
        ut["grunn"] = (f"{ver}. This node probably runs under containerd "
                       f"without the docker CLI - the rebuild has to be "
                       f"done locally.")
        return ut
    ut["compose_har_instrumentnett"] = _compose_har(repo, "instrumentnett")
    ut.update(sjekk_env(repo))
    try:
        with open(UT_FIL, "r", encoding="utf-8") as f:
            ut["siste_utdata"] = f.read()[-4000:]
    except Exception:
        pass
    return ut


def bygg_om() -> tuple:
    """Oppdater .env + compose-fila, og bygg om containeren. (ok, melding)

    Kommandoen blir køyrd lausrive: `docker compose up -d` river ned
    containeren denne koden lever i, så han må overleve vår eigen død.
    """
    st = status()
    if not st["repo"]:
        return False, st["grunn"] or "Could not locate the repo on the host"
    if not st["docker"]:
        return False, st["grunn"]
    if not st["trygt"]:
        return False, st["grunn"] or "Cannot confirm this is safe"

    repo = st["repo"]
    if st["manglar"]:
        feil = _skriv_env(repo, st["manglar"])
        if feil:
            return False, f"Could not update .env: {feil}"

    feil = oppdater_compose_fil(repo)
    if feil:
        return False, feil

    try:
        os.makedirs(os.path.dirname(UT_FIL), exist_ok=True)
        with open(UT_FIL, "w", encoding="utf-8") as f:
            f.write(f"Startar docker compose up -d i {repo}\n"
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    except Exception:
        pass

    kommando = (f"cd '{repo}' && docker compose up -d "
                f">> '{UT_FIL}' 2>&1; "
                f"echo '--- ferdig' >> '{UT_FIL}'")
    try:
        subprocess.Popen(_HOST_NS + ["setsid", "sh", "-c", kommando],
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception as e:
        return False, f"Could not start the rebuild: {e}"

    endra = ", ".join(f"{k}={v}" for k, v in st["manglar"].items())
    return True, ("Rebuilding the container - it is down for about half a "
                  "minute. "
                  + (f"Filled in .env: {endra}. " if endra else "")
                  + "Reload the page shortly.")
