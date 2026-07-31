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

log = logging.getLogger("wifi_manager")

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
def koble_til(ssid: str, passord: str = "", skjult: bool = False) -> tuple:
    """Kople verten til eit WiFi-nett. NetworkManager persisterer profilen.
    Returnerer (ok, melding). Passordet vert aldri logga."""
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "Manglar SSID"
    if not _har_nmcli():
        return False, "NetworkManager (nmcli) ikkje tilgjengeleg på verten."
    _radio_på()
    dev = _wifi_dev()

    cmd = ["device", "wifi", "connect", ssid]
    if passord:
        cmd += ["password", passord]
    if skjult:
        cmd += ["hidden", "yes"]
    cmd += ["ifname", dev]

    try:
        r = _nmcli(cmd, timeout=45)
    except Exception as e:
        return False, f"Tilkopling feila: {e}"
    if r.returncode == 0:
        log.info(f"WiFi kopla til SSID={ssid!r} på {dev}")
        return True, f"Kopla til «{ssid}»"
    feil = (r.stderr or r.stdout or "").strip()
    # Ikkje lek passord om nmcli skulle ekko kommandoen
    if passord:
        feil = feil.replace(passord, "***")
    return False, feil or "Tilkopling feila"


def gløym(ssid: str) -> tuple:
    """Slett den lagra profilen for eit nett."""
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "Manglar SSID"
    if not _har_nmcli():
        return False, "NetworkManager (nmcli) ikkje tilgjengeleg på verten."
    try:
        r = _nmcli(["connection", "delete", "id", ssid], timeout=15)
    except Exception as e:
        return False, str(e)
    if r.returncode == 0:
        return True, f"Gløymde «{ssid}»"
    return False, (r.stderr or r.stdout or "Kunne ikkje gløyme nettet").strip()
