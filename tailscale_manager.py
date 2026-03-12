#!/usr/bin/env python3
"""
Tailscale Manager
=================
Wrappar tailscale CLI for installasjon, aktivering og statussjekk.
Brukt av web_ui.py for /api/tailscale/* endepunkt.

Følgjer same mønster som usbip_manager.py.
"""

import os
import subprocess
import threading
import time
import json
import shutil

_lock = threading.Lock()

# Vanlege installasjonsstadar for tailscale
_TAILSCALE_PATHS = ["/usr/bin/tailscale", "/usr/sbin/tailscale", "/usr/local/bin/tailscale"]


def _kjor(cmd, timeout=15):
    """Køyr ein kommando og returner (stdout, stderr, returncode)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "Timeout", 1


def er_installert():
    """Sjekk om tailscale CLI finst (which + kjende stiar)."""
    if shutil.which("tailscale") is not None:
        return True
    # Fallback: sjekk kjende stiar direkte (PATH i Python-prosessen
    # inkluderer ikkje alltid /usr/sbin etter installasjon)
    return any(os.path.isfile(p) and os.access(p, os.X_OK) for p in _TAILSCALE_PATHS)


def installer():
    """Installer Tailscale via offisiell install-script.

    Returns:
        (suksess: bool, melding: str)
    """
    with _lock:
        if er_installert():
            return True, "Tailscale er allereie installert"

        out, err, rc = _kjor("curl -fsSL https://tailscale.com/install.sh | sh", timeout=120)
        if rc != 0:
            return False, f"Installasjon feila: {err or out}"

        # Oppdater PATH for denne prosessen slik at which() og
        # subprocess finn tailscale/tailscaled etter installasjon
        for d in ["/usr/bin", "/usr/sbin", "/usr/local/bin"]:
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = os.environ.get("PATH", "") + ":" + d

        if er_installert():
            return True, "Tailscale installert"
        return False, "Installasjon fullført men tailscale vart ikkje funne"


def avinstaller():
    """Avinstaller Tailscale.

    Stoppar daemon og fjernar pakken.

    Returns:
        (suksess: bool, melding: str)
    """
    with _lock:
        if not er_installert():
            return True, "Tailscale er ikkje installert"

        # Stopp daemon fyrst
        _kjor("tailscale down", timeout=10)
        _kjor("killall tailscaled", timeout=5)

        out, err, rc = _kjor(
            "apt-get remove --purge -y tailscale tailscaled 2>&1 && apt-get autoremove -y 2>&1",
            timeout=60,
        )
        if rc != 0:
            return False, f"Avinstallering feila: {err or out}"

        if not er_installert():
            return True, "Tailscale avinstallert"
        return False, "Avinstallering fullført men tailscale er framleis tilgjengeleg"


def hent_status():
    """Hent Tailscale-status som dict.

    Returnerer:
        {
            "installert": bool,
            "daemon_køyrer": bool,
            "tilkobla": bool,
            "ip": str | None,
            "hostname": str,
            "tailnet": str,
            "nodar": [{"hostname": ..., "ip": ..., "online": bool}],
            "feil": None | str
        }
    """
    status = {
        "installert": False,
        "daemon_køyrer": False,
        "tilkobla": False,
        "ip": None,
        "hostname": "",
        "tailnet": "",
        "nodar": [],
        "feil": None,
    }

    if not er_installert():
        status["feil"] = "Tailscale er ikkje installert"
        return status
    status["installert"] = True

    # Sjekk om tailscaled daemon køyrer
    _, _, rc = _kjor("pgrep tailscaled")
    if rc != 0:
        return status
    status["daemon_køyrer"] = True

    # Hent detaljert status frå tailscale
    out, err, rc = _kjor("tailscale status --json", timeout=10)
    if rc != 0:
        status["feil"] = err or "Kunne ikkje hente status"
        return status

    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError) as e:
        status["feil"] = f"Ugyldig JSON frå tailscale: {e}"
        return status

    # BackendState: Running, NeedsLogin, Stopped, etc.
    backend_state = data.get("BackendState", "")
    status["tilkobla"] = backend_state == "Running"

    # Eigen node-info
    self_node = data.get("Self", {})
    dns_name = self_node.get("DNSName", "")
    status["hostname"] = dns_name.rstrip(".").split(".")[0] if dns_name else ""

    # Tailnet-namn
    current_tailnet = data.get("CurrentTailnet", {})
    status["tailnet"] = current_tailnet.get("Name", "")

    # Eigen IP
    tailscale_ips = self_node.get("TailscaleIPs", [])
    for ip in tailscale_ips:
        if "." in ip:  # IPv4
            status["ip"] = ip
            break

    # Andre nodar i tailnet
    peers = data.get("Peer", {})
    for _key, peer in peers.items():
        peer_dns = peer.get("DNSName", "")
        peer_hostname = peer_dns.rstrip(".").split(".")[0] if peer_dns else peer.get("HostName", "")
        peer_ips = peer.get("TailscaleIPs", [])
        peer_ip = ""
        for ip in peer_ips:
            if "." in ip:
                peer_ip = ip
                break
        status["nodar"].append({
            "hostname": peer_hostname,
            "ip": peer_ip,
            "online": peer.get("Online", False),
        })

    return status


def start(authkey, hostname=None):
    """Start tailscaled daemon og koble til tailnet.

    Args:
        authkey: Tailscale auth key
        hostname: Valfritt hostname for denne noden

    Returns:
        (suksess: bool, melding: str)
    """
    with _lock:
        if not er_installert():
            return False, "Tailscale er ikkje installert i containeren"

        if not authkey:
            return False, "Auth key manglar"

        # 1. Sjekk om tailscaled allereie køyrer
        _, _, rc = _kjor("pgrep tailscaled")
        if rc != 0:
            # Start tailscaled daemon
            _kjor(
                "tailscaled "
                "--state=/data/tailscale/tailscaled.state "
                "--socket=/var/run/tailscale/tailscaled.sock &",
                timeout=5,
            )
            # Vent for daemon å starte
            time.sleep(2)

            # Verifiser at daemon starta
            _, _, rc2 = _kjor("pgrep tailscaled")
            if rc2 != 0:
                return False, "Kunne ikkje starte tailscaled daemon"

        # 2. tailscale up
        cmd = (
            f"tailscale up "
            f"--authkey={authkey} "
            f"--accept-routes"
        )
        if hostname:
            cmd += f" --hostname={hostname}"

        out, err, rc = _kjor(cmd, timeout=30)
        if rc != 0:
            return False, f"tailscale up feila: {err or out}"

        # 3. Verifiser tilkobling
        time.sleep(1)
        status = hent_status()
        if status["tilkobla"]:
            ip_msg = f" (IP: {status['ip']})" if status["ip"] else ""
            return True, f"Tailscale tilkobla{ip_msg}"
        else:
            return True, "Tailscale starta, ventar på tilkobling..."


def stopp():
    """Koble frå tailnet.

    Returns:
        (suksess: bool, melding: str)
    """
    with _lock:
        if not er_installert():
            return False, "Tailscale er ikkje installert"

        _, _, rc = _kjor("pgrep tailscaled")
        if rc != 0:
            return True, "Tailscale er allereie stengd"

        out, err, rc = _kjor("tailscale down", timeout=10)
        if rc != 0:
            return False, f"tailscale down feila: {err or out}"

        return True, "Tailscale fråkobla"


def hent_ip():
    """Hent Tailscale IPv4-adresse.

    Returns:
        str | None
    """
    if not er_installert():
        return None
    out, _, rc = _kjor("tailscale ip -4", timeout=5)
    if rc == 0 and out:
        return out.split()[0]
    return None
