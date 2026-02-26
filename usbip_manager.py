#!/usr/bin/env python3
"""
USB/IP Manager for Dewesoft SIRIUS
===================================
Styrer USB/IP-deling av SIRIUS fra web UI.
Pi deler SIRIUS USB over nettverket, Windows kobler til
og DewesoftX ser den som lokal USB-enhet.

Flyt:
  SIRIUS --USB--> Pi (usbipd :3240) --Ethernet--> Windows (usbip-win2 VHCI)
                                                         |
                                                   DewesoftX ser SIRIUS
                                                   som lokal USB
"""

import os
import subprocess
import logging
import threading

log = logging.getLogger("usbip_manager")

# Dewesoft USB VID/PID-er
DEWESOFT_VID = "1ced"
DEWESOFT_PIDS = {
    "1002": "SIRIUS",
    "2000": "BOX Power",
    "3001": "Battery Pack",
}
USBIP_PORT = 3240

# Global status (leses av web UI)
usbip_status = {
    "tilgjengelig": False,     # usbip-verktoy + moduler OK
    "deling_aktiv": False,     # bind + usbipd kjorer
    "busid": None,             # f.eks. "1-1.2"
    "enhet": None,             # f.eks. "SIRIUS (1ced:1002)"
    "feil": None,              # siste feilmelding
    "pid": None,               # usbipd prosess-ID
}

_lock = threading.Lock()
_usbipd_prosess = None


def sjekk_usbip():
    """Sjekk om usbip-verktoy og kernel-moduler er tilgjengelige."""
    # Sjekk usbip-kommando
    try:
        r = subprocess.run(
            ["usbip", "version"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            # Noen versjoner bruker --version
            r = subprocess.run(
                ["usbip", "--version"],
                capture_output=True, text=True, timeout=5
            )
    except FileNotFoundError:
        return False, "usbip-verktoy ikke installert"
    except subprocess.TimeoutExpired:
        return False, "usbip timeout"

    # Sjekk kernel-moduler via /sys
    moduler_ok = True
    mangler = []
    for modul in ["usbip_core", "usbip_host"]:
        if not os.path.isdir(f"/sys/module/{modul}"):
            # Proov med bindestrek-variant
            modul_alt = modul.replace("_", "-")
            if not os.path.isdir(f"/sys/module/{modul_alt}"):
                moduler_ok = False
                mangler.append(modul)

    if not moduler_ok:
        return False, f"Kernel-moduler mangler: {', '.join(mangler)}. Kjor setup_host.sh paa Pi-hosten."

    return True, "OK"


def finn_sirius_busid():
    """Finn SIRIUS paa USB-bussen via sysfs, returner (busid, enhetsnavn) eller (None, None)."""
    sysfs_base = "/sys/bus/usb/devices"

    if not os.path.isdir(sysfs_base):
        log.warning(f"sysfs ikke tilgjengelig: {sysfs_base}")
        return None, None

    for entry in os.listdir(sysfs_base):
        dev_path = os.path.join(sysfs_base, entry)
        vendor_file = os.path.join(dev_path, "idVendor")
        product_file = os.path.join(dev_path, "idProduct")

        if not os.path.isfile(vendor_file) or not os.path.isfile(product_file):
            continue

        try:
            with open(vendor_file, "r") as f:
                vid = f.read().strip()
            with open(product_file, "r") as f:
                pid = f.read().strip()
        except (IOError, OSError):
            continue

        if vid == DEWESOFT_VID and pid in DEWESOFT_PIDS:
            enhetsnavn = f"{DEWESOFT_PIDS[pid]} ({vid}:{pid})"
            busid = entry  # f.eks. "1-1.2"
            log.info(f"Funnet Dewesoft {enhetsnavn} paa busid={busid}")
            return busid, enhetsnavn

    log.info("Ingen Dewesoft-enhet funnet paa USB-bussen")
    return None, None


def _rydd_stale_usbip(busid=None):
    """Rydd opp stale usbip-tilstand: drep usbipd og unbind.

    Kernel usbip-host-driveren hugsar 'exported'-tilstand frå tidlegare
    sesjonar. Utan unbind+rebind får Windows-klienten 'Device busy
    (already exported)' ved attach. Denne funksjonen nullstiller alt.
    """
    global _usbipd_prosess

    # 1. Drep vår eigen usbipd-prosess
    if _usbipd_prosess and _usbipd_prosess.poll() is None:
        try:
            _usbipd_prosess.terminate()
            _usbipd_prosess.wait(timeout=3)
        except Exception:
            try:
                _usbipd_prosess.kill()
            except Exception:
                pass
        _usbipd_prosess = None

    # 2. Drep eventuelle andre usbipd-prosessar (frå tidlegare container-køyring)
    try:
        subprocess.run(["killall", "usbipd"], capture_output=True, timeout=5)
    except Exception:
        pass

    # 3. Unbind enheten for å nullstille 'exported'-tilstand
    target_busid = busid or usbip_status.get("busid")
    if target_busid:
        try:
            subprocess.run(
                ["usbip", "unbind", f"--busid={target_busid}"],
                capture_output=True, text=True, timeout=10
            )
            log.info(f"Rydda stale usbip bind: busid={target_busid}")
        except Exception:
            pass


def del_enhet():
    """Bind SIRIUS til usbip + start usbipd daemon paa port 3240.

    Ryddar ALLTID opp stale tilstand fyrst (unbind + kill usbipd) for å
    unngaa 'Device busy (already exported)' frå Windows-klienten.

    Returnerer (suksess, melding).
    """
    global _usbipd_prosess

    with _lock:
        # 1. Sjekk usbip-verktoy + moduler
        ok, melding = sjekk_usbip()
        if not ok:
            usbip_status.update({"tilgjengelig": False, "feil": melding})
            return False, melding

        usbip_status["tilgjengelig"] = True

        # 2. Finn SIRIUS
        busid, enhetsnavn = finn_sirius_busid()
        if not busid:
            feil = "SIRIUS ikke funnet paa USB. Sjekk at SIRIUS er koblet til Pi med USB-kabel."
            usbip_status["feil"] = feil
            return False, feil

        # 3. Rydd opp stale tilstand (drep gammal usbipd + unbind)
        _rydd_stale_usbip(busid)

        # 4. Bind enheten til usbip-host (friskt bind etter unbind)
        try:
            r = subprocess.run(
                ["usbip", "bind", f"--busid={busid}"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode != 0 and "already bound" not in r.stderr.lower():
                feil = f"usbip bind feilet: {r.stderr.strip()}"
                usbip_status["feil"] = feil
                return False, feil
            log.info(f"usbip bind OK: busid={busid}")
        except Exception as e:
            feil = f"usbip bind feilet: {e}"
            usbip_status["feil"] = feil
            return False, feil

        # 5. Start ny usbipd daemon
        try:
            _usbipd_prosess = subprocess.Popen(
                ["usbipd", "--tcp-port", str(USBIP_PORT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info(f"usbipd startet paa port {USBIP_PORT} (pid={_usbipd_prosess.pid})")
        except Exception as e:
            feil = f"Kunne ikke starte usbipd: {e}"
            usbip_status["feil"] = feil
            return False, feil

        # 6. Oppdater status
        usbip_status.update({
            "tilgjengelig": True,
            "deling_aktiv": True,
            "busid": busid,
            "enhet": enhetsnavn,
            "feil": None,
            "pid": _usbipd_prosess.pid if _usbipd_prosess else None,
        })

        return True, f"{enhetsnavn} deles paa port {USBIP_PORT} (busid={busid})"


def stopp_deling():
    """Unbind SIRIUS + stopp usbipd. Returnerer (suksess, melding)."""
    global _usbipd_prosess

    with _lock:
        busid = usbip_status.get("busid")

        # 1. Unbind enheten
        if busid:
            try:
                r = subprocess.run(
                    ["usbip", "unbind", f"--busid={busid}"],
                    capture_output=True, text=True, timeout=10
                )
                if r.returncode != 0:
                    log.warning(f"usbip unbind advarsel: {r.stderr.strip()}")
                else:
                    log.info(f"usbip unbind OK: busid={busid}")
            except Exception as e:
                log.warning(f"usbip unbind feilet: {e}")

        # 2. Stopp usbipd
        if _usbipd_prosess and _usbipd_prosess.poll() is None:
            try:
                _usbipd_prosess.terminate()
                _usbipd_prosess.wait(timeout=5)
                log.info("usbipd stoppet")
            except subprocess.TimeoutExpired:
                _usbipd_prosess.kill()
                log.info("usbipd drept (kill)")
            except Exception as e:
                log.warning(f"Kunne ikke stoppe usbipd: {e}")
            _usbipd_prosess = None
        else:
            # Proov aa stoppe eventuelle usbipd-prosesser startet utenfor
            try:
                subprocess.run(
                    ["killall", "usbipd"],
                    capture_output=True, timeout=5
                )
            except Exception:
                pass
            _usbipd_prosess = None

        # 3. Oppdater status
        usbip_status.update({
            "deling_aktiv": False,
            "busid": None,
            "enhet": None,
            "feil": None,
            "pid": None,
        })

        return True, "USB/IP-deling stoppet"


def hent_usbip_status():
    """Returner status-dict for web UI."""
    with _lock:
        # Sjekk om usbipd fortsatt kjorer
        if _usbipd_prosess and _usbipd_prosess.poll() is not None:
            # Prosessen har doed
            usbip_status.update({
                "deling_aktiv": False,
                "pid": None,
                "feil": "usbipd stoppet uventet",
            })

        # Sjekk tilgjengelighet
        ok, melding = sjekk_usbip()
        usbip_status["tilgjengelig"] = ok
        if not ok and not usbip_status.get("feil"):
            usbip_status["feil"] = melding

        # Sjekk om SIRIUS er paa USB (uavhengig av deling)
        busid, enhetsnavn = finn_sirius_busid()
        sirius_funnet = busid is not None

        return {
            **usbip_status,
            "sirius_paa_usb": sirius_funnet,
            "sirius_busid_funnet": busid,
            "sirius_enhet_funnet": enhetsnavn,
        }
