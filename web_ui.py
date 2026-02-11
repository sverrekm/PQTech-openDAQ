#!/usr/bin/env python3
"""
Web-grensesnitt for openDAQ Server (Dewesoft SIRIUS)
=====================================================
Flask-app som viser openDAQ server-status og gir
instruksjoner for tilkobling fra DewesoftX.

Kjor: python3 web_ui.py
Apne: http://<pi-ip>:8080
"""

import os
import subprocess
import socket
import threading
import glob as glob_mod

from flask import Flask, jsonify, request, send_file

import usbip_manager

# Betinget import av SIRIUS-driver (kun tilgjengelig i NATIVE_SIRIUS-modus)
try:
    from sirius_server import (
        hent_driver_status as _sirius_hent_status,
        hent_enhetsinfo as _sirius_hent_info,
        start_driver_streaming as _sirius_start,
        stopp_driver_streaming as _sirius_stopp,
        hent_siste_data as _sirius_hent_data,
        rekoble_driver as _sirius_rekoble,
        hent_logg as _sirius_hent_logg,
        send_debug_kommando as _sirius_debug_cmd,
        frigjor_usb as _sirius_frigjor_usb,
        hent_opendaq_status as _opendaq_hent_status,
    )
    SIRIUS_DIREKTE = True
except ImportError:
    SIRIUS_DIREKTE = False

app = Flask(__name__)


def kjor(cmd):
    """Kjor en kommando og returner output."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "Timeout", 1


def hent_ip():
    """Finn maskinens IP-adresse."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        out, _, _ = kjor("hostname -I")
        return out.split()[0] if out else "ukjent"


def hent_status():
    """Hent openDAQ server-status."""
    # Sjekk om opendaq_server.py kjorer
    _, _, rc = kjor("pgrep -f opendaq_server.py")
    server_kjorer = rc == 0

    # Proov aa lese status fra opendaq_server modulen
    enhet_navn = ""
    kanaler = []
    servere = []

    siste_maaling = None
    antall_maalinger = 0
    autonom = False

    try:
        from opendaq_server import server_status
        enhet_navn = server_status.get("enhet_navn", "")
        kanaler = server_status.get("kanaler", [])
        servere = server_status.get("servere", [])
        siste_maaling = server_status.get("siste_maaling")
        antall_maalinger = server_status.get("antall_maalinger", 0)
        autonom = server_status.get("autonom", False)
        if server_status.get("kjorer"):
            server_kjorer = True
    except Exception:
        pass

    # Sjekk USB-enheter
    usb_enheter = []
    out, _, _ = kjor("lsusb 2>/dev/null")
    for linje in out.splitlines():
        if linje.strip():
            usb_enheter.append(linje.strip())

    return {
        "server_kjorer": server_kjorer,
        "ip": hent_ip(),
        "enhet_navn": enhet_navn,
        "kanaler": kanaler,
        "servere": servere,
        "usb_enheter": usb_enheter,
        "siste_maaling": siste_maaling,
        "antall_maalinger": antall_maalinger,
        "autonom": autonom,
    }


# --- API ---

@app.route("/api/status")
def api_status():
    return jsonify(hent_status())


@app.route("/api/enheter")
def api_enheter():
    """Returnerer liste over oppdagede openDAQ-enheter."""
    try:
        from opendaq_server import hent_tilgjengelige_enheter
        return jsonify({"enheter": hent_tilgjengelige_enheter()})
    except Exception as e:
        return jsonify({"enheter": [], "feil": str(e)})


@app.route("/api/koble-til", methods=["POST"])
def api_koble_til():
    """Koble til en enhet med gitt tilkoblingsstreng."""
    data = request.get_json(silent=True) or {}
    tilkobling = data.get("tilkobling", "").strip()
    if not tilkobling:
        return jsonify({"suksess": False, "melding": "Mangler tilkoblingsstreng"}), 400
    try:
        from opendaq_server import koble_til_enhet
        suksess, melding = koble_til_enhet(tilkobling)
        return jsonify({"suksess": suksess, "melding": melding})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


# --- SIRIUS USB Probe API ---

_probe_resultat = {"status": "idle", "output": "", "rapport": None}
_probe_lock = threading.Lock()


@app.route("/api/probe/kjor", methods=["POST"])
def api_probe_kjor():
    """Kjor SIRIUS USB probe i bakgrunnen."""
    with _probe_lock:
        if _probe_resultat["status"] == "running":
            return jsonify({"suksess": False, "melding": "Probe kjorer allerede"})
        _probe_resultat.update({"status": "running", "output": "", "rapport": None})

    def _kjor_probe():
        try:
            r = subprocess.run(
                ["python3", "/app/sirius_usb_probe.py", "--full", "--debug"],
                capture_output=True, text=True, timeout=30,
                cwd="/app"
            )
            with _probe_lock:
                _probe_resultat.update({
                    "status": "done",
                    "output": r.stdout + ("\n--- STDERR ---\n" + r.stderr if r.stderr else ""),
                    "returncode": r.returncode,
                })
        except subprocess.TimeoutExpired:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": "Timeout (30s)"})
        except Exception as e:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": str(e)})

    threading.Thread(target=_kjor_probe, daemon=True).start()
    return jsonify({"suksess": True, "melding": "Probe startet"})


@app.route("/api/probe/status")
def api_probe_status():
    """Returnerer probe-status og output."""
    with _probe_lock:
        return jsonify(dict(_probe_resultat))


@app.route("/api/probe/protokoll", methods=["POST"])
def api_probe_protokoll():
    """Kjor SIRIUS protokoll-skanning (kommandoskanning + datastroemmer)."""
    data = request.get_json(silent=True) or {}
    modus = data.get("modus", "full")  # full, scan, stream, multi

    with _probe_lock:
        if _probe_resultat["status"] == "running":
            return jsonify({"suksess": False, "melding": "Probe kjorer allerede"})
        _probe_resultat.update({"status": "running", "output": "", "rapport": None})

    flagg = {"full": "--full", "scan": "--scan", "stream": "--stream", "multi": "--multi"}
    flagg_arg = flagg.get(modus, "--full")

    def _kjor():
        try:
            r = subprocess.run(
                ["python3", "/app/sirius_protokoll.py", flagg_arg, "--debug"],
                capture_output=True, text=True, timeout=120,
                cwd="/app"
            )
            with _probe_lock:
                _probe_resultat.update({
                    "status": "done",
                    "output": r.stdout + ("\n--- STDERR ---\n" + r.stderr if r.stderr else ""),
                    "returncode": r.returncode,
                })
        except subprocess.TimeoutExpired:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": "Timeout (120s)"})
        except Exception as e:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": str(e)})

    threading.Thread(target=_kjor, daemon=True).start()
    return jsonify({"suksess": True, "melding": f"Protokoll-skanning startet ({modus})"})


@app.route("/api/probe/dekoder", methods=["POST"])
def api_probe_dekoder():
    """Kjor SIRIUS dekoder (dypere protokollanalyse)."""
    data = request.get_json(silent=True) or {}
    modus = data.get("modus", "full")

    with _probe_lock:
        if _probe_resultat["status"] == "running":
            return jsonify({"suksess": False, "melding": "Probe kjorer allerede"})
        _probe_resultat.update({"status": "running", "output": "", "rapport": None})

    flagg = {"full": "--full", "info": "--info", "stream": "--stream",
             "explore": "--explore", "status": "--status"}
    flagg_arg = flagg.get(modus, "--full")

    def _kjor():
        try:
            r = subprocess.run(
                ["python3", "/app/sirius_dekoder.py", flagg_arg, "--debug"],
                capture_output=True, text=True, timeout=120,
                cwd="/app"
            )
            with _probe_lock:
                _probe_resultat.update({
                    "status": "done",
                    "output": r.stdout + ("\n--- STDERR ---\n" + r.stderr if r.stderr else ""),
                    "returncode": r.returncode,
                })
        except subprocess.TimeoutExpired:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": "Timeout (120s)"})
        except Exception as e:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": str(e)})

    threading.Thread(target=_kjor, daemon=True).start()
    return jsonify({"suksess": True, "melding": f"Dekoder startet ({modus})"})


@app.route("/api/probe/adc", methods=["POST"])
def api_probe_adc():
    """Les ADC-data fra SIRIUS og analyser kanalstruktur."""
    data = request.get_json(silent=True) or {}
    varighet = min(data.get("varighet", 5), 30)
    lagre = data.get("lagre", False)

    with _probe_lock:
        if _probe_resultat["status"] == "running":
            return jsonify({"suksess": False, "melding": "Probe kjorer allerede"})
        _probe_resultat.update({"status": "running", "output": "", "rapport": None})

    cmd_args = ["python3", "/app/sirius_adc_leser.py",
                "--varighet", str(varighet), "--kanaler", "--raa"]
    if lagre:
        cmd_args.append("--lagre")

    def _kjor():
        try:
            r = subprocess.run(
                cmd_args, capture_output=True, text=True, timeout=60, cwd="/app"
            )
            with _probe_lock:
                _probe_resultat.update({
                    "status": "done",
                    "output": r.stdout + ("\n--- STDERR ---\n" + r.stderr if r.stderr else ""),
                    "returncode": r.returncode,
                })
        except subprocess.TimeoutExpired:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": "Timeout (60s)"})
        except Exception as e:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": str(e)})

    threading.Thread(target=_kjor, daemon=True).start()
    return jsonify({"suksess": True, "melding": f"ADC-lesing startet ({varighet}s)"})


@app.route("/api/probe/sniffer", methods=["POST"])
def api_probe_sniffer():
    """Start passiv USB-trafikkfangst (forstyrrer IKKE USB/IP)."""
    data = request.get_json(silent=True) or {}
    varighet = min(data.get("varighet", 15), 60)

    with _probe_lock:
        if _probe_resultat["status"] == "running":
            return jsonify({"suksess": False, "melding": "Probe kjorer allerede"})
        _probe_resultat.update({"status": "running", "output": "", "rapport": None})

    def _kjor():
        try:
            r = subprocess.run(
                ["python3", "/app/sirius_sniffer.py",
                 "--varighet", str(varighet), "--debug"],
                capture_output=True, text=True, timeout=int(varighet) + 30,
                cwd="/app"
            )
            with _probe_lock:
                _probe_resultat.update({
                    "status": "done",
                    "output": r.stdout + ("\n--- STDERR ---\n" + r.stderr if r.stderr else ""),
                    "returncode": r.returncode,
                })
        except subprocess.TimeoutExpired:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": "Timeout"})
        except Exception as e:
            with _probe_lock:
                _probe_resultat.update({"status": "error", "output": str(e)})

    threading.Thread(target=_kjor, daemon=True).start()
    return jsonify({"suksess": True, "melding": f"Sniffer startet ({varighet}s)"})


@app.route("/api/probe/rapporter")
def api_probe_rapporter():
    """List tilgjengelige rapportfiler."""
    rapporter = []
    for moenster in ["sirius_*.json", "sirius_*.csv", "sirius_*.npz"]:
        for fil in glob_mod.glob(os.path.join("/app", moenster)):
            rapporter.append({
                "filnavn": os.path.basename(fil),
                "storrelse": os.path.getsize(fil),
                "endret": os.path.getmtime(fil),
            })
    rapporter.sort(key=lambda r: r["endret"], reverse=True)
    return jsonify({"rapporter": rapporter})


@app.route("/api/probe/last-ned/<filnavn>")
def api_probe_last_ned(filnavn):
    """Last ned en rapportfil."""
    # Sikkerhet: kun filer i /app som starter med sirius_
    if not filnavn.startswith("sirius_") or ".." in filnavn:
        return jsonify({"feil": "Ugyldig filnavn"}), 400
    fil_path = os.path.join("/app", filnavn)
    if not os.path.isfile(fil_path):
        return jsonify({"feil": "Fil ikke funnet"}), 404
    return send_file(fil_path, as_attachment=True)


# --- SIRIUS Direkte API ---

@app.route("/api/sirius/status")
def api_sirius_status():
    """Driver-status, streaming, daterate."""
    if not SIRIUS_DIREKTE:
        return jsonify({"tilgjengelig": False, "melding": "SIRIUS direkte-driver ikke lastet"})
    try:
        status = _sirius_hent_status()
        status["tilgjengelig"] = True
        return jsonify(status)
    except Exception as e:
        return jsonify({"tilgjengelig": True, "feil": str(e)})


@app.route("/api/sirius/info")
def api_sirius_info():
    """Enhetsidentifikasjon (serienr, firmware, slotter)."""
    if not SIRIUS_DIREKTE:
        return jsonify({"feil": "SIRIUS direkte-driver ikke lastet"}), 503
    try:
        return jsonify(_sirius_hent_info())
    except Exception as e:
        return jsonify({"feil": str(e)}), 500


@app.route("/api/sirius/start", methods=["POST"])
def api_sirius_start():
    """Start streaming."""
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False, "melding": "Driver ikke lastet"}), 503
    data = request.get_json(silent=True) or {}
    sample_rate = data.get("sample_rate")
    kanaler = data.get("kanaler")
    try:
        suksess, melding = _sirius_start(sample_rate, kanaler)
        return jsonify({"suksess": suksess, "melding": melding})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/sirius/stopp", methods=["POST"])
def api_sirius_stopp():
    """Stopp streaming."""
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False, "melding": "Driver ikke lastet"}), 503
    try:
        suksess, melding = _sirius_stopp()
        return jsonify({"suksess": suksess, "melding": melding})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/sirius/data")
def api_sirius_data():
    """Siste data-snapshot (per-kanal verdier)."""
    if not SIRIUS_DIREKTE:
        return jsonify({"feil": "Driver ikke lastet"}), 503
    try:
        return jsonify(_sirius_hent_data())
    except Exception as e:
        return jsonify({"feil": str(e)}), 500


@app.route("/api/sirius/rekoble", methods=["POST"])
def api_sirius_rekoble():
    """Proev aa koble til paa nytt."""
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False, "melding": "Driver ikke lastet"}), 503
    try:
        suksess, melding = _sirius_rekoble()
        return jsonify({"suksess": suksess, "melding": melding})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


# --- Logg og Debug API ---

@app.route("/api/logg")
def api_logg():
    """Returner dei siste logg-linjene (ring buffer)."""
    if not SIRIUS_DIREKTE:
        return jsonify({"linjer": [], "feil": "SIRIUS-driver ikkje lasta"})
    antall = request.args.get("antall", 200, type=int)
    linjer = _sirius_hent_logg(min(antall, 500))
    return jsonify({"linjer": linjer, "antall": len(linjer)})


@app.route("/api/debug/kommando", methods=["POST"])
def api_debug_kommando():
    """Send ein raa USB-kommando og returner hex-svar."""
    if not SIRIUS_DIREKTE:
        return jsonify({"feil": "SIRIUS-driver ikkje lasta"}), 503
    data = request.get_json(silent=True) or {}
    kommando = data.get("kommando", "").strip()
    if not kommando:
        return jsonify({"feil": "Mangler 'kommando' (hex-streng)"}), 400
    poll = data.get("poll", False)
    resultat = _sirius_debug_cmd(kommando, poll=poll)
    return jsonify(resultat)


# --- openDAQ Bridge API ---

@app.route("/api/opendaq/status")
def api_opendaq_status():
    """openDAQ bridge status (servere, portar, DewesoftX-tilkobling)."""
    if not SIRIUS_DIREKTE:
        return jsonify({"tilgjengelig": False, "melding": "openDAQ bridge ikkje lasta"})
    try:
        return jsonify(_opendaq_hent_status())
    except Exception as e:
        return jsonify({"tilgjengelig": False, "feil": str(e)})


# --- USB/IP API ---

@app.route("/api/usbip/status")
def api_usbip_status():
    """Returnerer USB/IP-status."""
    return jsonify(usbip_manager.hent_usbip_status())


@app.route("/api/usbip/del", methods=["POST"])
def api_usbip_del():
    """Start USB-deling (bind + usbipd).

    Frigjer SIRIUS fraa native driver fyrst (dei er gjensidig ekskluderande).
    """
    # Frigjor USB fraa native driver foerst
    if SIRIUS_DIREKTE:
        ok, msg = _sirius_frigjor_usb()
        if not ok:
            return jsonify({"suksess": False, "melding": f"Kunne ikkje frigjere USB: {msg}"})

    import time
    time.sleep(1)  # Kort pause slik at USB-enheten vert tilgjengeleg

    suksess, melding = usbip_manager.del_enhet()
    status = usbip_manager.hent_usbip_status()
    return jsonify({"suksess": suksess, "melding": melding, "status": status})


@app.route("/api/usbip/stopp", methods=["POST"])
def api_usbip_stopp():
    """Stopp USB-deling (unbind) og rekoblar native driver."""
    suksess, melding = usbip_manager.stopp_deling()

    # Rekoblar native driver automatisk
    rekoble_msg = ""
    if SIRIUS_DIREKTE:
        import time
        time.sleep(1)
        try:
            ok, rmsg = _sirius_rekoble()
            rekoble_msg = f" Native driver: {'rekobla' if ok else rmsg}"
        except Exception as e:
            rekoble_msg = f" Native driver: feil ({e})"

    status = usbip_manager.hent_usbip_status()
    return jsonify({
        "suksess": suksess,
        "melding": melding + rekoble_msg,
        "status": status,
    })


# --- HTML ---

@app.route("/")
def index():
    return HTML_SIDE


HTML_SIDE = """<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PQTech - Dewesoft SIRIUS</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f5f5f5;
    color: #1a1a1a;
    min-height: 100vh;
}
.header {
    background: #1a1a1a;
    border-bottom: 3px solid #D76428;
    padding: 1rem 1.5rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.header h1 { font-size: 1.25rem; font-weight: 600; color: #ffffff; }
.header h1 span { color: #D76428; }
.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.75rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 500;
}
.status-ok { background: #d1fae5; color: #065f46; }
.status-feil { background: #fee2e2; color: #991b1b; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot-gronn { background: #10b981; animation: puls 2s infinite; }
.dot-rod { background: #ef4444; }
@keyframes puls { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.main { max-width: 900px; margin: 0 auto; padding: 1.5rem; }
.kort {
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 0.75rem;
    padding: 1.25rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.kort h2 {
    font-size: 0.85rem;
    color: #6b6b6b;
    margin-bottom: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}
.info-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0.75rem;
}
.info-boks {
    background: #f5f5f5;
    border-radius: 0.5rem;
    padding: 0.75rem 1rem;
}
.info-boks .label { font-size: 0.75rem; color: #6b6b6b; }
.info-boks .verdi { font-size: 1.1rem; font-weight: 600; margin-top: 0.25rem; color: #1a1a1a; }
.cmd-boks {
    background: #f5f5f5;
    border: 1px solid #e0e0e0;
    border-radius: 0.5rem;
    padding: 1rem;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 0.85rem;
    color: #B85420;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-top: 0.75rem;
}
.cmd-boks code { flex: 1; word-break: break-all; }
.btn-kopier {
    background: #e0e0e0;
    color: #1a1a1a;
    border: none;
    padding: 0.35rem 0.7rem;
    border-radius: 0.25rem;
    cursor: pointer;
    font-size: 0.75rem;
    white-space: nowrap;
}
.btn-kopier:hover { background: #d0d0d0; }
.steg {
    counter-reset: steg;
    list-style: none;
    padding: 0;
}
.steg li {
    counter-increment: steg;
    padding: 0.75rem 0 0.75rem 3rem;
    position: relative;
    border-bottom: 1px solid #f0f0f0;
    font-size: 0.9rem;
    color: #4a4a4a;
}
.steg li:last-child { border-bottom: none; }
.steg li::before {
    content: counter(steg);
    position: absolute;
    left: 0;
    width: 2rem;
    height: 2rem;
    background: #FDF2EC;
    color: #D76428;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 600;
    font-size: 0.85rem;
}
.tag {
    display: inline-block;
    padding: 0.2rem 0.5rem;
    border-radius: 0.25rem;
    font-size: 0.75rem;
    font-weight: 500;
}
.tag-aktiv { background: #d1fae5; color: #065f46; }
.tag-usb { background: #FDF2EC; color: #D76428; }
.kanal-liste {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.5rem;
}
.usb-liste {
    list-style: none;
    padding: 0;
}
.usb-liste li {
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid #f0f0f0;
    font-size: 0.85rem;
    font-family: 'Consolas', monospace;
    color: #6b6b6b;
}
.usb-liste li.sirius { color: #D76428; font-weight: 600; }
.spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid #e0e0e0;
    border-top-color: #D76428;
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }
.koble-rad {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.75rem;
}
.koble-input {
    flex: 1;
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 0.5rem;
    padding: 0.6rem 1rem;
    font-family: 'Consolas', monospace;
    font-size: 0.85rem;
    color: #1a1a1a;
    outline: none;
}
.koble-input:focus { border-color: #D76428; box-shadow: 0 0 0 2px #FDF2EC; }
.koble-input::placeholder { color: #aaa; }
.btn {
    border: none;
    padding: 0.6rem 1rem;
    border-radius: 0.5rem;
    cursor: pointer;
    font-size: 0.8rem;
    font-weight: 500;
    white-space: nowrap;
    transition: background 150ms ease;
}
.btn-gronn { background: #D76428; color: #ffffff; }
.btn-gronn:hover { background: #B85420; }
.btn-blaa { background: #f5f5f5; color: #1a1a1a; border: 1px solid #e0e0e0; }
.btn-blaa:hover { background: #e8e8e8; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.enhet-liste {
    list-style: none;
    padding: 0;
    margin-top: 0.75rem;
}
.enhet-liste li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid #f0f0f0;
    font-size: 0.85rem;
}
.enhet-liste .enhet-info {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
}
.enhet-liste .enhet-navn { color: #1a1a1a; font-weight: 500; }
.enhet-liste .enhet-conn { color: #6b6b6b; font-family: 'Consolas', monospace; font-size: 0.75rem; }
.melding {
    margin-top: 0.75rem;
    padding: 0.5rem 0.75rem;
    border-radius: 0.5rem;
    font-size: 0.8rem;
    display: none;
}
.melding-ok { background: #d1fae5; color: #065f46; display: block; }
.melding-feil { background: #fee2e2; color: #991b1b; display: block; }
.btn-rod { background: #fee2e2; color: #991b1b; }
.btn-rod:hover { background: #fecaca; }
.usbip-status-rad {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
    font-size: 0.9rem;
}
.usbip-knapper {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.75rem;
}
.usbip-instruksjoner {
    background: #f5f5f5;
    border: 1px solid #e0e0e0;
    border-radius: 0.5rem;
    padding: 1rem;
    margin-top: 1rem;
}
.usbip-instruksjoner h3 {
    font-size: 0.85rem;
    color: #6b6b6b;
    margin-bottom: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    font-weight: 600;
}
.usbip-instruksjoner ol {
    list-style: decimal;
    padding-left: 1.25rem;
}
.usbip-instruksjoner li {
    font-size: 0.85rem;
    color: #4a4a4a;
    padding: 0.25rem 0;
}
.usbip-instruksjoner code {
    background: #FDF2EC;
    padding: 0.15rem 0.4rem;
    border-radius: 0.25rem;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 0.8rem;
    color: #B85420;
}
</style>
</head>
<body>

<div class="header">
    <h1><span>PQTech</span> &mdash; Dewesoft SIRIUS</h1>
    <div id="status-badge" class="status-badge status-feil">
        <span class="dot dot-rod" id="status-dot"></span>
        <span id="status-tekst">Sjekker...</span>
    </div>
</div>

<div class="main">

    <div class="kort">
        <h2>Server</h2>
        <div class="info-grid">
            <div class="info-boks">
                <div class="label">IP-adresse</div>
                <div class="verdi" id="info-ip">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Enhet</div>
                <div class="verdi" id="info-enhet">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Protokoller</div>
                <div class="verdi" id="info-servere">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Kanaler</div>
                <div class="verdi" id="info-kanaler">-</div>
            </div>
        </div>
        <div id="kanal-tags" class="kanal-liste"></div>
    </div>

    <div class="kort">
        <h2>Autonom maaling</h2>
        <div class="info-grid">
            <div class="info-boks">
                <div class="label">Status</div>
                <div class="verdi" id="info-autonom">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Antall maalinger</div>
                <div class="verdi" id="info-antall">0</div>
            </div>
            <div class="info-boks">
                <div class="label">Siste maaling</div>
                <div class="verdi" id="info-siste" style="font-size:0.85rem;">-</div>
            </div>
        </div>
        <p style="color:#6b6b6b; font-size:0.8rem; margin-top:0.75rem;">
            Pi maaler og lagrer data lokalt, uavhengig av DewesoftX-tilkobling.
            Filer lagres i <code style="color:#B85420;">/data/maalinger/</code>
        </p>
    </div>

    <div class="kort" id="sirius-direkte-kort" style="display:none;">
        <h2>SIRIUS Direkte</h2>
        <div class="info-grid">
            <div class="info-boks">
                <div class="label">Tilkobling</div>
                <div class="verdi" id="sirius-tilkobling">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Serienummer</div>
                <div class="verdi" id="sirius-serienr">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Streaming</div>
                <div class="verdi" id="sirius-streaming">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Daterate</div>
                <div class="verdi" id="sirius-daterate">-</div>
            </div>
        </div>
        <div id="sirius-slot-info" style="margin-top:0.75rem;"></div>
        <div id="sirius-kanal-verdier" style="margin-top:0.75rem; font-family:'Consolas',monospace; font-size:0.85rem;"></div>
        <div style="display:flex; gap:0.5rem; margin-top:0.75rem;">
            <button class="btn btn-gronn" id="btn-sirius-start" onclick="siriusStart()">Start streaming</button>
            <button class="btn btn-rod" id="btn-sirius-stopp" onclick="siriusStopp()" style="display:none;">Stopp streaming</button>
            <button class="btn btn-blaa" id="btn-sirius-rekoble" onclick="siriusRekoble()">Rekoble</button>
        </div>
        <div id="sirius-melding" class="melding"></div>
    </div>

    <div class="kort" id="opendaq-bro-kort" style="display:none;">
        <h2>openDAQ Nettverksservere</h2>
        <div class="info-grid">
            <div class="info-boks">
                <div class="label">Status</div>
                <div class="verdi" id="odaq-status">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Enhet</div>
                <div class="verdi" id="odaq-enhet">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Kanalar</div>
                <div class="verdi" id="odaq-kanalar">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Servere</div>
                <div class="verdi" id="odaq-servere">-</div>
            </div>
        </div>
        <div class="info-grid" style="margin-top:0.5rem;">
            <div class="info-boks">
                <div class="label">OPC-UA</div>
                <div class="verdi" style="font-size:0.75rem;" id="odaq-opcua">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Native Streaming</div>
                <div class="verdi" style="font-size:0.75rem;" id="odaq-ns">-</div>
            </div>
            <div class="info-boks">
                <div class="label">WebSocket</div>
                <div class="verdi" style="font-size:0.75rem;" id="odaq-ws">-</div>
            </div>
        </div>
        <div class="cmd-boks" style="margin-top:0.75rem;">
            <code id="odaq-dewesoftx-addr">-</code>
            <button class="btn-kopier" onclick="kopierTekst('odaq-dewesoftx-addr')">Kopier</button>
        </div>
        <p style="color:#6b6b6b; font-size:0.8rem; margin-top:0.5rem;">
            DewesoftX: Settings &gt; Devices &gt; Dewesoft NET &gt; Manually add &gt; skriv inn adressa over.
            <br>Fase 1: Referanse-enhet med simulerte kanalar.
        </p>
    </div>

    <div class="kort">
        <h2>Koble til enhet</h2>
        <div class="koble-rad">
            <input type="text" id="tilkobling-input" class="koble-input"
                   placeholder="daq.opcua://192.168.1.X">
            <button class="btn btn-gronn" id="btn-koble" onclick="kobleTil()">Koble til</button>
            <button class="btn btn-blaa" id="btn-sok" onclick="sokEnheter()">Sok</button>
        </div>
        <div id="koble-melding" class="melding"></div>
        <ul class="enhet-liste" id="enhet-liste"></ul>
        <p style="color:#6b6b6b; font-size:0.8rem; margin-top:0.75rem;">
            Skriv inn tilkoblingsstreng eller klikk Sok for aa finne enheter paa nettverket.
            Eksempler: <code style="color:#B85420;">daq.opcua://IP</code>,
            <code style="color:#B85420;">daq.ns://IP</code>,
            <code style="color:#B85420;">daqref://device0</code>
        </p>
    </div>

    <div class="kort" id="dewesoft-kort">
        <h2>Koble til fra DewesoftX</h2>
        <ol class="steg">
            <li>Apne DewesoftX paa Windows-PC</li>
            <li>Gaa til <strong>Settings &gt; Devices</strong></li>
            <li>Under <strong>Dewesoft NET</strong>, klikk <em>manually add measurement unit</em></li>
            <li>Skriv inn adressen:</li>
        </ol>
        <div class="cmd-boks">
            <code id="pi-adresse">-</code>
            <button class="btn-kopier" onclick="kopier()">Kopier</button>
        </div>
        <p style="color:#6b6b6b; font-size:0.8rem; margin-top:0.75rem;">
            Klikk OK &mdash; SIRIUS dukker opp under Detected devices.
        </p>
    </div>

    <div class="kort" id="probe-kort">
        <h2>SIRIUS USB-analyse</h2>

        <div style="padding:0.5rem 0.75rem; background:#FDF2EC; border:1px solid #E8753A;
             border-radius:0.5rem; display:flex; align-items:center; gap:0.75rem; margin-bottom:0.75rem;">
            <button class="btn" id="btn-sniffer" onclick="kjorSniffer(15)"
                    style="background:#D76428;color:#ffffff;font-weight:600;white-space:nowrap;">
                Fang DewesoftX-trafikk (15s)
            </button>
            <span style="color:#B85420; font-size:0.8rem;">
                Passiv fangst via usbmon &mdash; forstyrrer IKKE USB/IP eller DewesoftX.
            </span>
        </div>

        <details style="margin-bottom:0.75rem;">
            <summary style="cursor:pointer; color:#6b6b6b; font-size:0.85rem;">
                Direkte USB-tester (stopper USB/IP-deling!)
            </summary>
            <p style="color:#991b1b; font-size:0.8rem; margin:0.5rem 0;">
                Disse verktoyene tar kontroll over SIRIUS USB og avbryter USB/IP.
                DewesoftX mister tilkoblingen. Bruk kun naar USB/IP er stoppet.
            </p>
            <div class="usbip-knapper">
                <button class="btn btn-blaa" id="btn-probe" onclick="kjorProbe()">
                    USB Deskriptorer
                </button>
                <button class="btn btn-blaa" id="btn-proto-scan" onclick="kjorProtokoll('scan')">
                    Skann kommandoer
                </button>
                <button class="btn btn-blaa" id="btn-dekod-info" onclick="kjorDekoder('info')">
                    Dekod enhetsinfo
                </button>
                <button class="btn" id="btn-adc" onclick="kjorAdc(5)"
                        style="background:#E8753A;color:#ffffff;font-weight:600;">
                    Les ADC (5s)
                </button>
                <button class="btn" id="btn-adc-lagre" onclick="kjorAdc(10, true)"
                        style="background:#D76428;color:#ffffff;font-weight:600;">
                    Les + Lagre (10s)
                </button>
            </div>
        </details>

        <div id="probe-status" class="melding" style="display:none;"></div>
        <pre id="probe-output" style="display:none; background:#1a1a1a; border:1px solid #e0e0e0;
             border-radius:0.5rem; padding:1rem; margin-top:0.75rem; font-size:0.75rem;
             color:#E8753A; max-height:400px; overflow-y:auto; white-space:pre-wrap;
             font-family:'Consolas','Monaco',monospace;"></pre>

        <div id="rapporter-seksjon" style="margin-top:0.75rem;">
            <button class="btn btn-blaa" onclick="hentRapporter()" style="font-size:0.8rem;">
                Vis rapporter
            </button>
            <ul id="rapport-liste" class="usb-liste" style="margin-top:0.5rem;"></ul>
        </div>
    </div>

    <div class="kort" id="usbip-kort">
        <h2>USB/IP &mdash; DEL SIRIUS</h2>
        <div class="usbip-status-rad">
            <span class="dot" id="usbip-dot"></span>
            <span id="usbip-status-tekst">Sjekker...</span>
        </div>
        <div class="info-grid">
            <div class="info-boks">
                <div class="label">SIRIUS paa USB</div>
                <div class="verdi" id="usbip-sirius-funnet">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Bus-ID</div>
                <div class="verdi" id="usbip-busid">-</div>
            </div>
            <div class="info-boks">
                <div class="label">Deling</div>
                <div class="verdi" id="usbip-deling">-</div>
            </div>
        </div>
        <div id="usbip-feil" class="melding"></div>
        <div class="usbip-knapper">
            <button class="btn btn-gronn" id="btn-usbip-del" onclick="usbipDel()">
                Del SIRIUS via USB/IP
            </button>
            <button class="btn btn-rod" id="btn-usbip-stopp" onclick="usbipStopp()" style="display:none;">
                Stopp deling
            </button>
        </div>
        <div class="usbip-instruksjoner" id="usbip-instruksjoner" style="display:none;">
            <h3>Paa Windows-PC</h3>
            <ol>
                <li>Installer <a href="https://github.com/cezanne/usbip-win2/releases" target="_blank" style="color:#D76428;">usbip-win2</a></li>
                <li>Apne PowerShell som Administrator</li>
                <li>List enheter:
                    <div class="cmd-boks" style="margin-top:0.35rem;">
                        <code id="usbip-cmd-list">usbip list -r <span id="usbip-pi-ip">-</span></code>
                        <button class="btn-kopier" onclick="kopierTekst('usbip-cmd-list')">Kopier</button>
                    </div>
                </li>
                <li>Koble til SIRIUS:
                    <div class="cmd-boks" style="margin-top:0.35rem;">
                        <code id="usbip-cmd-attach">usbip attach -r <span id="usbip-pi-ip2">-</span> -b <span id="usbip-attach-busid">X-Y</span></code>
                        <button class="btn-kopier" onclick="kopierTekst('usbip-cmd-attach')">Kopier</button>
                    </div>
                </li>
                <li>Apne DewesoftX &mdash; SIRIUS vises som lokal USB-enhet</li>
            </ol>
        </div>
    </div>

</div>

<script>
async function hentData() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        oppdaterUI(data);
    } catch (e) {
        console.error('Feil:', e);
    }
}

function oppdaterUI(s) {
    // Status-badge
    const badge = document.getElementById('status-badge');
    const dot = document.getElementById('status-dot');
    const tekst = document.getElementById('status-tekst');
    if (s.server_kjorer) {
        badge.className = 'status-badge status-ok';
        dot.className = 'dot dot-gronn';
        tekst.textContent = 'Aktiv';
    } else {
        badge.className = 'status-badge status-feil';
        dot.className = 'dot dot-rod';
        tekst.textContent = 'Stoppet';
    }

    // Server-info
    document.getElementById('info-ip').textContent = s.ip || '-';
    document.getElementById('info-enhet').textContent = s.enhet_navn || 'Soker...';
    document.getElementById('info-servere').textContent =
        s.servere.length > 0 ? s.servere.join(', ') : '-';
    document.getElementById('info-kanaler').textContent =
        s.kanaler.length > 0 ? s.kanaler.length : '-';

    // Kanal-tags
    const tags = document.getElementById('kanal-tags');
    if (s.kanaler.length > 0) {
        tags.innerHTML = s.kanaler.map(k =>
            `<span class="tag tag-aktiv">${esc(k)}</span>`
        ).join('');
    } else {
        tags.innerHTML = '';
    }

    // Autonom maaling
    document.getElementById('info-autonom').textContent =
        s.autonom ? 'Aktiv' : 'Inaktiv';
    document.getElementById('info-antall').textContent = s.antall_maalinger || '0';
    document.getElementById('info-siste').textContent =
        s.siste_maaling ? new Date(s.siste_maaling).toLocaleString('no-NO') : '-';

    // DewesoftX adresse
    document.getElementById('pi-adresse').textContent = s.ip || '-';

}

// --- USB/IP ---
async function hentUsbipStatus() {
    try {
        const res = await fetch('/api/usbip/status');
        const data = await res.json();
        oppdaterUsbipUI(data);
    } catch (e) {
        console.error('USB/IP feil:', e);
    }
}

function oppdaterUsbipUI(u) {
    const dot = document.getElementById('usbip-dot');
    const tekst = document.getElementById('usbip-status-tekst');
    const btnDel = document.getElementById('btn-usbip-del');
    const btnStopp = document.getElementById('btn-usbip-stopp');
    const instruksjoner = document.getElementById('usbip-instruksjoner');
    const feilEl = document.getElementById('usbip-feil');

    // SIRIUS funnet paa USB
    const siriusFunnet = document.getElementById('usbip-sirius-funnet');
    siriusFunnet.textContent = u.sirius_paa_usb ? (u.sirius_enhet_funnet || 'Ja') : 'Nei';
    siriusFunnet.style.color = u.sirius_paa_usb ? '#10b981' : '#ef4444';

    // Bus-ID
    document.getElementById('usbip-busid').textContent = u.busid || u.sirius_busid_funnet || '-';

    // Deling-status
    const delingEl = document.getElementById('usbip-deling');
    if (u.deling_aktiv) {
        dot.className = 'dot dot-gronn';
        tekst.textContent = 'Deling aktiv paa port 3240';
        delingEl.textContent = 'Aktiv';
        delingEl.style.color = '#10b981';
        btnDel.style.display = 'none';
        btnStopp.style.display = 'inline-block';
        instruksjoner.style.display = 'block';
        // Oppdater kommandoer med riktig IP og busid
        const ip = document.getElementById('info-ip').textContent || '-';
        document.getElementById('usbip-pi-ip').textContent = ip;
        document.getElementById('usbip-pi-ip2').textContent = ip;
        document.getElementById('usbip-attach-busid').textContent = u.busid || 'X-Y';
    } else {
        dot.className = 'dot dot-rod';
        tekst.textContent = u.tilgjengelig ? 'Klar' : 'USB/IP utilgjengelig';
        delingEl.textContent = 'Inaktiv';
        delingEl.style.color = '#6b6b6b';
        btnDel.style.display = 'inline-block';
        btnDel.disabled = !u.sirius_paa_usb || !u.tilgjengelig;
        btnStopp.style.display = 'none';
        instruksjoner.style.display = 'none';
    }

    // Feilmelding
    if (u.feil) {
        feilEl.textContent = u.feil;
        feilEl.className = 'melding melding-feil';
    } else {
        feilEl.className = 'melding';
        feilEl.textContent = '';
    }
}

async function usbipDel() {
    const btn = document.getElementById('btn-usbip-del');
    btn.disabled = true;
    btn.textContent = 'Starter deling...';
    try {
        const res = await fetch('/api/usbip/del', {method: 'POST'});
        const data = await res.json();
        if (data.status) oppdaterUsbipUI(data.status);
        if (!data.suksess) {
            const feilEl = document.getElementById('usbip-feil');
            feilEl.textContent = data.melding;
            feilEl.className = 'melding melding-feil';
        }
    } catch (e) {
        const feilEl = document.getElementById('usbip-feil');
        feilEl.textContent = 'Nettverksfeil: ' + e.message;
        feilEl.className = 'melding melding-feil';
    }
    btn.disabled = false;
    btn.textContent = 'Del SIRIUS via USB/IP';
}

async function usbipStopp() {
    const btn = document.getElementById('btn-usbip-stopp');
    btn.disabled = true;
    btn.textContent = 'Stopper...';
    try {
        const res = await fetch('/api/usbip/stopp', {method: 'POST'});
        const data = await res.json();
        if (data.status) oppdaterUsbipUI(data.status);
    } catch (e) {
        console.error('Stopp feil:', e);
    }
    btn.disabled = false;
    btn.textContent = 'Stopp deling';
}

function kopierTekst(elementId) {
    const el = document.getElementById(elementId);
    navigator.clipboard.writeText(el.textContent);
}

function kopier() {
    const tekst = document.getElementById('pi-adresse').textContent;
    navigator.clipboard.writeText(tekst);
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

async function sokEnheter() {
    const btn = document.getElementById('btn-sok');
    const liste = document.getElementById('enhet-liste');
    btn.disabled = true;
    btn.textContent = 'Soker...';
    liste.innerHTML = '<li><span class="spinner"></span> Soker etter enheter...</li>';
    visMelding('');
    try {
        const res = await fetch('/api/enheter');
        const data = await res.json();
        if (data.enheter && data.enheter.length > 0) {
            liste.innerHTML = data.enheter.map(e =>
                `<li>
                    <div class="enhet-info">
                        <span class="enhet-navn">${esc(e.navn)}</span>
                        <span class="enhet-conn">${esc(e.tilkobling)}</span>
                    </div>
                    <button class="btn btn-gronn" onclick="kobleTilEnhet('${esc(e.tilkobling).replace(/'/g, "\\'")}')">Koble til</button>
                </li>`
            ).join('');
        } else {
            liste.innerHTML = '<li style="color:#6b6b6b;">Ingen enheter funnet</li>';
        }
    } catch (e) {
        liste.innerHTML = '<li style="color:#ef4444;">Feil ved sok</li>';
    }
    btn.disabled = false;
    btn.textContent = 'Sok';
}

function kobleTilEnhet(conn) {
    document.getElementById('tilkobling-input').value = conn;
    kobleTil();
}

async function kobleTil() {
    const input = document.getElementById('tilkobling-input');
    const btn = document.getElementById('btn-koble');
    const tilkobling = input.value.trim();
    if (!tilkobling) {
        visMelding('Skriv inn en tilkoblingsstreng', true);
        return;
    }
    btn.disabled = true;
    btn.textContent = 'Kobler til...';
    visMelding('');
    try {
        const res = await fetch('/api/koble-til', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({tilkobling: tilkobling})
        });
        const data = await res.json();
        visMelding(data.melding, !data.suksess);
        if (data.suksess) {
            hentData();
        }
    } catch (e) {
        visMelding('Nettverksfeil: ' + e.message, true);
    }
    btn.disabled = false;
    btn.textContent = 'Koble til';
}

function visMelding(tekst, erFeil) {
    const el = document.getElementById('koble-melding');
    if (!tekst) {
        el.className = 'melding';
        el.textContent = '';
        return;
    }
    el.textContent = tekst;
    el.className = 'melding ' + (erFeil ? 'melding-feil' : 'melding-ok');
}

// --- USB Probe ---
let probePolling = null;

async function kjorProbe() {
    const btn = document.getElementById('btn-probe');
    const statusEl = document.getElementById('probe-status');
    const outputEl = document.getElementById('probe-output');
    btn.disabled = true;
    btn.textContent = 'Kjorer probe...';
    statusEl.textContent = 'Starter USB probe...';
    statusEl.className = 'melding melding-ok';
    statusEl.style.display = 'block';
    outputEl.style.display = 'block';
    outputEl.textContent = 'Venter paa resultat...\\n';

    try {
        const res = await fetch('/api/probe/kjor', {method: 'POST'});
        const data = await res.json();
        if (!data.suksess) {
            statusEl.textContent = data.melding;
            statusEl.className = 'melding melding-feil';
            btn.disabled = false;
            btn.textContent = 'Kjor USB Probe';
            return;
        }
    } catch (e) {
        statusEl.textContent = 'Nettverksfeil: ' + e.message;
        statusEl.className = 'melding melding-feil';
        btn.disabled = false;
        btn.textContent = 'Kjor USB Probe';
        return;
    }

    // Poll for resultat
    probePolling = setInterval(async () => {
        try {
            const res = await fetch('/api/probe/status');
            const data = await res.json();
            if (data.status === 'done' || data.status === 'error') {
                clearInterval(probePolling);
                probePolling = null;
                outputEl.textContent = data.output || '(tomt resultat)';
                statusEl.textContent = data.status === 'done'
                    ? 'Probe fullfort (returncode: ' + (data.returncode || 0) + ')'
                    : 'Probe feilet';
                statusEl.className = 'melding ' + (data.status === 'done' ? 'melding-ok' : 'melding-feil');
                btn.disabled = false;
                btn.textContent = 'Kjor USB Probe';
            } else if (data.status === 'running') {
                outputEl.textContent = 'Probe kjorer...\\n' + (data.output || '');
            }
        } catch (e) {
            // Ignorer midlertidige feil
        }
    }, 1000);
}

async function kjorSniffer(varighet) {
    const statusEl = document.getElementById('probe-status');
    const outputEl = document.getElementById('probe-output');
    const alleBtns = ['btn-probe', 'btn-proto-scan', 'btn-dekod-info',
                      'btn-adc', 'btn-adc-lagre', 'btn-sniffer'];
    alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = true; });
    statusEl.textContent = 'Passiv USB-fangst (' + varighet + 's) - DewesoftX forstyrres IKKE...';
    statusEl.className = 'melding melding-ok';
    statusEl.style.display = 'block';
    outputEl.style.display = 'block';
    outputEl.textContent = 'Fanger USB-trafikk mellom DewesoftX og SIRIUS...\\n';

    try {
        const res = await fetch('/api/probe/sniffer', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({varighet: varighet})
        });
        const data = await res.json();
        if (!data.suksess) {
            statusEl.textContent = data.melding;
            statusEl.className = 'melding melding-feil';
            alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
            return;
        }
    } catch (e) {
        statusEl.textContent = 'Nettverksfeil: ' + e.message;
        statusEl.className = 'melding melding-feil';
        alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
        return;
    }

    if (probePolling) clearInterval(probePolling);
    probePolling = setInterval(async () => {
        try {
            const res = await fetch('/api/probe/status');
            const data = await res.json();
            if (data.status === 'done' || data.status === 'error') {
                clearInterval(probePolling);
                probePolling = null;
                outputEl.textContent = data.output || '(tomt resultat)';
                statusEl.textContent = data.status === 'done' ? 'Fangst fullfort' : 'Fangst feilet';
                statusEl.className = 'melding ' + (data.status === 'done' ? 'melding-ok' : 'melding-feil');
                alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
            } else if (data.status === 'running') {
                outputEl.textContent = 'Fanger trafikk...\\n' + (data.output || '');
            }
        } catch (e) {}
    }, 2000);
}

async function kjorAdc(varighet, lagre) {
    const statusEl = document.getElementById('probe-status');
    const outputEl = document.getElementById('probe-output');
    const alleBtns = ['btn-probe', 'btn-proto-scan', 'btn-dekod-info',
                      'btn-adc', 'btn-adc-lagre', 'btn-sniffer'];
    alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = true; });
    statusEl.textContent = 'Leser ADC-data (' + varighet + 's)...';
    statusEl.className = 'melding melding-ok';
    statusEl.style.display = 'block';
    outputEl.style.display = 'block';
    outputEl.textContent = 'Leser fra SIRIUS...\\n';

    try {
        const res = await fetch('/api/probe/adc', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({varighet: varighet, lagre: !!lagre})
        });
        const data = await res.json();
        if (!data.suksess) {
            statusEl.textContent = data.melding;
            statusEl.className = 'melding melding-feil';
            alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
            return;
        }
    } catch (e) {
        statusEl.textContent = 'Nettverksfeil: ' + e.message;
        statusEl.className = 'melding melding-feil';
        alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
        return;
    }

    if (probePolling) clearInterval(probePolling);
    probePolling = setInterval(async () => {
        try {
            const res = await fetch('/api/probe/status');
            const data = await res.json();
            if (data.status === 'done' || data.status === 'error') {
                clearInterval(probePolling);
                probePolling = null;
                outputEl.textContent = data.output || '(tomt resultat)';
                statusEl.textContent = data.status === 'done' ? 'ADC-lesing fullfort' : 'ADC-lesing feilet';
                statusEl.className = 'melding ' + (data.status === 'done' ? 'melding-ok' : 'melding-feil');
                alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
            } else if (data.status === 'running') {
                outputEl.textContent = 'Leser ADC-data...\\n' + (data.output || '');
            }
        } catch (e) {}
    }, 1500);
}

async function kjorDekoder(modus) {
    const statusEl = document.getElementById('probe-status');
    const outputEl = document.getElementById('probe-output');
    const alleBtns = ['btn-probe', 'btn-proto-scan', 'btn-dekod-info',
                      'btn-adc', 'btn-adc-lagre', 'btn-sniffer'];
    alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = true; });
    statusEl.textContent = 'Starter dekoder (' + modus + ')...';
    statusEl.className = 'melding melding-ok';
    statusEl.style.display = 'block';
    outputEl.style.display = 'block';
    outputEl.textContent = 'Venter paa resultat...\\n';

    try {
        const res = await fetch('/api/probe/dekoder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({modus: modus})
        });
        const data = await res.json();
        if (!data.suksess) {
            statusEl.textContent = data.melding;
            statusEl.className = 'melding melding-feil';
            alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
            return;
        }
    } catch (e) {
        statusEl.textContent = 'Nettverksfeil: ' + e.message;
        statusEl.className = 'melding melding-feil';
        alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
        return;
    }

    if (probePolling) clearInterval(probePolling);
    probePolling = setInterval(async () => {
        try {
            const res = await fetch('/api/probe/status');
            const data = await res.json();
            if (data.status === 'done' || data.status === 'error') {
                clearInterval(probePolling);
                probePolling = null;
                outputEl.textContent = data.output || '(tomt resultat)';
                statusEl.textContent = data.status === 'done' ? 'Dekoding fullfort' : 'Dekoding feilet';
                statusEl.className = 'melding ' + (data.status === 'done' ? 'melding-ok' : 'melding-feil');
                alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
            } else if (data.status === 'running') {
                outputEl.textContent = 'Dekoder kjorer...\\n' + (data.output || '');
            }
        } catch (e) {}
    }, 1500);
}

async function kjorProtokoll(modus) {
    const btn = document.getElementById('btn-proto-' + modus);
    const statusEl = document.getElementById('probe-status');
    const outputEl = document.getElementById('probe-output');
    const alleBtns = ['btn-probe', 'btn-proto-scan', 'btn-dekod-info',
                      'btn-adc', 'btn-adc-lagre', 'btn-sniffer'];
    alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = true; });
    btn.textContent = 'Kjorer...';
    statusEl.textContent = 'Starter protokoll-skanning (' + modus + ')...';
    statusEl.className = 'melding melding-ok';
    statusEl.style.display = 'block';
    outputEl.style.display = 'block';
    outputEl.textContent = 'Venter paa resultat...\\n';

    try {
        const res = await fetch('/api/probe/protokoll', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({modus: modus})
        });
        const data = await res.json();
        if (!data.suksess) {
            statusEl.textContent = data.melding;
            statusEl.className = 'melding melding-feil';
            alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
            return;
        }
    } catch (e) {
        statusEl.textContent = 'Nettverksfeil: ' + e.message;
        statusEl.className = 'melding melding-feil';
        alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
        return;
    }

    // Poll for resultat (gjenbruk probe-polling)
    if (probePolling) clearInterval(probePolling);
    probePolling = setInterval(async () => {
        try {
            const res = await fetch('/api/probe/status');
            const data = await res.json();
            if (data.status === 'done' || data.status === 'error') {
                clearInterval(probePolling);
                probePolling = null;
                outputEl.textContent = data.output || '(tomt resultat)';
                statusEl.textContent = data.status === 'done'
                    ? 'Analyse fullfort'
                    : 'Analyse feilet';
                statusEl.className = 'melding ' + (data.status === 'done' ? 'melding-ok' : 'melding-feil');
                alleBtns.forEach(id => { const b = document.getElementById(id); if(b) b.disabled = false; });
                btn.textContent = btn.dataset.originalText || btn.textContent;
            } else if (data.status === 'running') {
                outputEl.textContent = 'Analyse kjorer...\\n' + (data.output || '');
            }
        } catch (e) {}
    }, 1500);
}

async function hentRapporter() {
    const liste = document.getElementById('rapport-liste');
    liste.innerHTML = '<li>Henter...</li>';
    try {
        const res = await fetch('/api/probe/rapporter');
        const data = await res.json();
        if (data.rapporter && data.rapporter.length > 0) {
            liste.innerHTML = data.rapporter.map(r => {
                const kb = (r.storrelse / 1024).toFixed(1);
                return `<li><a href="/api/probe/last-ned/${r.filnavn}" style="color:#D76428;"
                    download>${r.filnavn}</a> (${kb} KB)</li>`;
            }).join('');
        } else {
            liste.innerHTML = '<li style="color:#6b6b6b;">Ingen rapporter</li>';
        }
    } catch (e) {
        liste.innerHTML = '<li style="color:#ef4444;">Feil</li>';
    }
}

// --- SIRIUS Direkte ---
async function hentSiriusStatus() {
    try {
        const res = await fetch('/api/sirius/status');
        const data = await res.json();
        oppdaterSiriusUI(data);
    } catch (e) {}
}

function oppdaterSiriusUI(s) {
    const kort = document.getElementById('sirius-direkte-kort');
    if (!s.tilgjengelig) {
        kort.style.display = 'none';
        return;
    }
    kort.style.display = 'block';

    const tilk = document.getElementById('sirius-tilkobling');
    tilk.textContent = s.tilkoblet ? 'Tilkoblet (USB)' : 'Frakoblet';
    tilk.style.color = s.tilkoblet ? '#10b981' : '#ef4444';

    document.getElementById('sirius-serienr').textContent = s.serienummer || '-';

    const stream = document.getElementById('sirius-streaming');
    stream.textContent = s.streamer ? 'Aktiv' : 'Inaktiv';
    stream.style.color = s.streamer ? '#10b981' : '#6b6b6b';

    const rate = document.getElementById('sirius-daterate');
    rate.textContent = s.data_rate_kbs > 0 ? s.data_rate_kbs.toFixed(1) + ' KB/s' : '-';

    // Vis/skjul knapper
    document.getElementById('btn-sirius-start').style.display = s.streamer ? 'none' : 'inline-block';
    document.getElementById('btn-sirius-stopp').style.display = s.streamer ? 'inline-block' : 'none';

    // Slot-info
    const slotEl = document.getElementById('sirius-slot-info');
    if (s.slot_info && s.slot_info.length > 0) {
        slotEl.innerHTML = '<div class="kanal-liste">' +
            s.slot_info.map(slot =>
                `<span class="tag ${slot.aktiv ? 'tag-aktiv' : 'tag-usb'}">` +
                `Slot ${slot.kanal}${slot.aktiv ? '' : ' (inaktiv)'}</span>`
            ).join('') + '</div>';
    }

    // Hent live data hvis streaming
    if (s.streamer) hentSiriusData();
}

async function hentSiriusData() {
    try {
        const res = await fetch('/api/sirius/data');
        const data = await res.json();
        const el = document.getElementById('sirius-kanal-verdier');
        if (Object.keys(data).length === 0) {
            el.textContent = '';
            return;
        }
        el.innerHTML = Object.entries(data).map(([k, v]) =>
            `<div style="color:#B85420;">${esc(k)}: <span style="color:#10b981;">${v.siste !== null ? v.siste : '-'}</span> (${v.antall} samples)</div>`
        ).join('');
    } catch (e) {}
}

async function siriusStart() {
    const btn = document.getElementById('btn-sirius-start');
    btn.disabled = true;
    btn.textContent = 'Starter...';
    try {
        const res = await fetch('/api/sirius/start', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: '{}'});
        const data = await res.json();
        const mel = document.getElementById('sirius-melding');
        mel.textContent = data.melding;
        mel.className = 'melding ' + (data.suksess ? 'melding-ok' : 'melding-feil');
    } catch (e) {
        const mel = document.getElementById('sirius-melding');
        mel.textContent = 'Feil: ' + e.message;
        mel.className = 'melding melding-feil';
    }
    btn.disabled = false;
    btn.textContent = 'Start streaming';
    hentSiriusStatus();
}

async function siriusStopp() {
    const btn = document.getElementById('btn-sirius-stopp');
    btn.disabled = true;
    btn.textContent = 'Stopper...';
    try {
        await fetch('/api/sirius/stopp', {method: 'POST'});
    } catch (e) {}
    btn.disabled = false;
    btn.textContent = 'Stopp streaming';
    hentSiriusStatus();
}

async function siriusRekoble() {
    const btn = document.getElementById('btn-sirius-rekoble');
    btn.disabled = true;
    btn.textContent = 'Rekobler...';
    try {
        const res = await fetch('/api/sirius/rekoble', {method: 'POST'});
        const data = await res.json();
        const mel = document.getElementById('sirius-melding');
        mel.textContent = data.melding;
        mel.className = 'melding ' + (data.suksess ? 'melding-ok' : 'melding-feil');
    } catch (e) {}
    btn.disabled = false;
    btn.textContent = 'Rekoble';
    hentSiriusStatus();
}

async function hentOpendaqStatus() {
    try {
        const res = await fetch('/api/opendaq/status');
        const s = await res.json();
        const kort = document.getElementById('opendaq-bro-kort');
        if (!s.tilgjengelig && !s.aktiv) { kort.style.display='none'; return; }
        kort.style.display='block';
        const st = document.getElementById('odaq-status');
        st.textContent = s.aktiv ? 'Aktiv' : 'Inaktiv';
        st.style.color = s.aktiv ? '#10b981' : '#ef4444';
        document.getElementById('odaq-enhet').textContent = s.enhet_namn || '-';
        document.getElementById('odaq-kanalar').textContent =
            s.kanalar && s.kanalar.length > 0 ? s.kanalar.length : '-';
        document.getElementById('odaq-servere').textContent =
            s.servere && s.servere.length > 0 ? s.servere.length : '-';
        const ip = s.ip || '-';
        const p = s.porter || {};
        document.getElementById('odaq-opcua').textContent = ip + ':' + (p.opcua || 4840);
        document.getElementById('odaq-ns').textContent = ip + ':' + (p.native_streaming || 7420);
        document.getElementById('odaq-ws').textContent = ip + ':' + (p.websocket || 7414);
        document.getElementById('odaq-dewesoftx-addr').textContent = ip;
    } catch(e) {}
}

hentData();
hentUsbipStatus();
hentSiriusStatus();
hentOpendaqStatus();
setInterval(hentData, 5000);
setInterval(hentUsbipStatus, 5000);
setInterval(hentSiriusStatus, 3000);
setInterval(hentOpendaqStatus, 5000);
</script>

</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port)
