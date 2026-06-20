#!/usr/bin/env python3
"""
NAS-manager — oppdag og monter nettverkslagring (SMB/CIFS) frå GUI
==================================================================
Lèt hubben (og nodar) oppdage SMB/CIFS-delingar på nettet og montere ei
valt deling — alt styrt frå web-GUI, utan host-tilgang. Containeren er
`privileged`, så den kan skanne (TCP 445 + smbclient) og montere CIFS sjølv.

Mountet er ikkje-persistent (forsvinn ved container-restart), så konfig
lagrast i /data/konfig/nas.json og remountast ved oppstart (monter_frå_konfig).
Passord lagrast i ei root-berre cred-fil (/data/konfig/.nas-cred).

Standard mountpunkt: /data/nas (same som rå-fil-arkivet brukar). Krev
`cifs-utils` + `smbclient` i imaget (Dockerfile) og at host-kjernen har
cifs-modulen (entrypoint kan modprobe).
"""

import os
import re
import json
import socket
import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("nas_manager")

KONFIG_STI = Path("/data/konfig/nas.json")
CRED_STI = Path("/data/konfig/.nas-cred")
STANDARD_MNT = "/data/nas"


# ---------------------------------------------------------------
#  Konfig
# ---------------------------------------------------------------
def les_konfig() -> dict:
    d = {"server": "", "share": "", "mountpunkt": STANDARD_MNT,
         "brukar": "", "domene": "", "automonter": False}
    try:
        if KONFIG_STI.exists():
            d.update(json.loads(KONFIG_STI.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning(f"Kunne ikkje lese nas-konfig: {e}")
    if not d.get("mountpunkt"):
        d["mountpunkt"] = STANDARD_MNT
    return d


def _lagre_konfig(d: dict) -> None:
    KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
    KONFIG_STI.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def konfig_offentleg() -> dict:
    d = les_konfig()
    d.pop("passord", None)  # aldri returner passord
    d["passord_satt"] = CRED_STI.exists()
    d.update(status())
    return d


# ---------------------------------------------------------------
#  Oppdaging: skann LAN for SMB-vertar + list delingar
# ---------------------------------------------------------------
def _mitt_subnet() -> str:
    """Returner /24-prefiks for containeren sin LAN-IP, t.d. '192.168.1.'."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip.rsplit(".", 1)[0] + "."
    except Exception:
        return "192.168.1."


def _port_open(ip: str, port: int = 445, timeout: float = 0.7) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        r = s.connect_ex((ip, port))
        s.close()
        return r == 0
    except Exception:
        return False


def _list_shares(ip: str, brukar: str = "", passord: str = "") -> list:
    """List Disk-delingar på ein SMB-vert via smbclient. Tom liste ved feil."""
    cmd = ["smbclient", "-L", f"//{ip}", "-g", "-t", "5"]
    if brukar:
        cmd += ["-U", f"{brukar}%{passord}"]
    else:
        cmd += ["-N"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=12).stdout
    except Exception:
        return []
    shares = []
    for ln in out.splitlines():
        # format: Disk|<namn>|<komment>
        if ln.startswith("Disk|"):
            namn = ln.split("|")[1].strip()
            if namn and not namn.endswith("$"):  # hopp over admin-delingar (print$, IPC$)
                shares.append(namn)
    return shares


def oppdag(brukar: str = "", passord: str = "") -> dict:
    """Skann LAN for SMB-vertar og list delingar. Returnerer
    {vertar: [{ip, hostname, shares: [..]}], subnet}."""
    prefiks = _mitt_subnet()
    funne = []
    lock = threading.Lock()

    def sjekk(x):
        ip = f"{prefiks}{x}"
        if _port_open(ip):
            with lock:
                funne.append(ip)

    traadar = [threading.Thread(target=sjekk, args=(x,)) for x in range(1, 255)]
    for t in traadar:
        t.start()
    for t in traadar:
        t.join(timeout=3)

    vertar = []
    for ip in sorted(funne, key=lambda s: int(s.rsplit(".", 1)[1])):
        hostname = ""
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            pass
        shares = _list_shares(ip, brukar, passord)
        vertar.append({"ip": ip, "hostname": hostname, "shares": shares})
    return {"subnet": f"{prefiks}0/24", "vertar": vertar}


# ---------------------------------------------------------------
#  Montering
# ---------------------------------------------------------------
def _er_montert(mountpunkt: str) -> bool:
    try:
        out = subprocess.run(["mountpoint", "-q", mountpunkt]).returncode
        return out == 0
    except Exception:
        # Fallback: sjekk /proc/mounts
        try:
            return any(f" {mountpunkt} " in ln for ln in
                       Path("/proc/mounts").read_text().splitlines())
        except Exception:
            return False


def _skriv_cred(brukar: str, passord: str, domene: str = "") -> None:
    CRED_STI.parent.mkdir(parents=True, exist_ok=True)
    linjer = [f"username={brukar}", f"password={passord}"]
    if domene:
        linjer.append(f"domain={domene}")
    CRED_STI.write_text("\n".join(linjer) + "\n", encoding="utf-8")
    try:
        os.chmod(CRED_STI, 0o600)
    except Exception:
        pass


def monter(server: str, share: str, brukar: str = "", passord: str = "",
           mountpunkt: str = STANDARD_MNT, domene: str = "",
           lagre: bool = True) -> tuple:
    """Monter //server/share på mountpunkt via CIFS. Returnerer (ok, melding).
    server kan vere IP eller hostname; share utan leiande slash."""
    server = server.strip().lstrip("/").rstrip("/")
    share = share.strip().strip("/")
    if not server or not share:
        return False, "Manglar server eller share"
    mp = mountpunkt or STANDARD_MNT
    unc = f"//{server}/{share}"

    # Prøv å laste cifs-kjernemodulen (host-namespace) — ufarleg viss alt lasta
    try:
        subprocess.run(["nsenter", "-t", "1", "-m", "-u", "-n", "-i",
                        "modprobe", "cifs"], capture_output=True, timeout=8)
    except Exception:
        try:
            subprocess.run(["modprobe", "cifs"], capture_output=True, timeout=8)
        except Exception:
            pass

    Path(mp).mkdir(parents=True, exist_ok=True)
    if _er_montert(mp):
        try:
            subprocess.run(["umount", mp], capture_output=True, timeout=15)
        except Exception:
            pass

    if passord:
        _skriv_cred(brukar, passord, domene)
    opts = f"credentials={CRED_STI},uid=0,gid=0,file_mode=0664,dir_mode=0775,iocharset=utf8,vers=3.0,nofail"
    if not CRED_STI.exists():
        opts = "guest,uid=0,gid=0,iocharset=utf8,vers=3.0,nofail"

    try:
        r = subprocess.run(["mount", "-t", "cifs", unc, mp, "-o", opts],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        return False, f"mount-feil: {e}"
    if r.returncode != 0:
        feil = (r.stderr or r.stdout or "").strip()
        # Prøv vers=2.1 / 1.0 fallback ved protokoll-feil
        if "vers" in feil.lower() or "protocol" in feil.lower() or "mount error(112)" in feil:
            for v in ("2.1", "1.0", "3.1.1"):
                opts_v = opts.replace("vers=3.0", f"vers={v}")
                r2 = subprocess.run(["mount", "-t", "cifs", unc, mp, "-o", opts_v],
                                    capture_output=True, text=True, timeout=30)
                if r2.returncode == 0:
                    feil = ""
                    break
            else:
                return False, f"mount feila: {feil}"
        else:
            return False, f"mount feila: {feil}"

    if lagre:
        _lagre_konfig({"server": server, "share": share, "mountpunkt": mp,
                       "brukar": brukar, "domene": domene, "automonter": True})
    log.info(f"NAS montert: {unc} → {mp}")
    return True, f"Montert {unc} → {mp}"


def avmonter() -> tuple:
    d = les_konfig()
    mp = d.get("mountpunkt", STANDARD_MNT)
    try:
        subprocess.run(["umount", mp], capture_output=True, timeout=15)
    except Exception as e:
        return False, str(e)
    d["automonter"] = False
    _lagre_konfig(d)
    return True, f"Avmontert {mp}"


def monter_frå_konfig() -> None:
    """Remount NAS ved oppstart viss konfigurert (automonter)."""
    d = les_konfig()
    if not d.get("automonter") or not d.get("server") or not d.get("share"):
        return
    if _er_montert(d.get("mountpunkt", STANDARD_MNT)):
        return
    ok, melding = monter(d["server"], d["share"], d.get("brukar", ""), "",
                         d.get("mountpunkt", STANDARD_MNT), d.get("domene", ""),
                         lagre=False)
    log.info(f"NAS auto-remount: {melding}")


def status() -> dict:
    d = les_konfig()
    mp = d.get("mountpunkt", STANDARD_MNT)
    ut = {"montert": _er_montert(mp), "mountpunkt": mp,
          "server": d.get("server", ""), "share": d.get("share", ""),
          "ledig_gb": None, "total_gb": None, "skrivbar": False}
    if ut["montert"]:
        try:
            st = os.statvfs(mp)
            ut["total_gb"] = round(st.f_blocks * st.f_frsize / 1e9, 1)
            ut["ledig_gb"] = round(st.f_bavail * st.f_frsize / 1e9, 1)
            ut["skrivbar"] = os.access(mp, os.W_OK)
        except Exception:
            pass
    return ut


def start() -> None:
    """Kall ved oppstart — remount NAS i bakgrunnen (ikkje blokker)."""
    threading.Thread(target=monter_frå_konfig, daemon=True, name="nas_remount").start()
