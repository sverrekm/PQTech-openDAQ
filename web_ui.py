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

from flask import Flask, jsonify, request

import usbip_manager

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


# --- USB/IP API ---

@app.route("/api/usbip/status")
def api_usbip_status():
    """Returnerer USB/IP-status."""
    return jsonify(usbip_manager.hent_usbip_status())


@app.route("/api/usbip/del", methods=["POST"])
def api_usbip_del():
    """Start USB-deling (bind + usbipd)."""
    suksess, melding = usbip_manager.del_enhet()
    status = usbip_manager.hent_usbip_status()
    return jsonify({"suksess": suksess, "melding": melding, "status": status})


@app.route("/api/usbip/stopp", methods=["POST"])
def api_usbip_stopp():
    """Stopp USB-deling (unbind)."""
    suksess, melding = usbip_manager.stopp_deling()
    status = usbip_manager.hent_usbip_status()
    return jsonify({"suksess": suksess, "melding": melding, "status": status})


# --- HTML ---

@app.route("/")
def index():
    return HTML_SIDE


HTML_SIDE = """<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>openDAQ Server - Dewesoft SIRIUS</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
}
.header {
    background: #1e293b;
    border-bottom: 2px solid #3b82f6;
    padding: 1rem 1.5rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.header h1 { font-size: 1.25rem; font-weight: 600; color: #f8fafc; }
.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.75rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 500;
}
.status-ok { background: #064e3b; color: #6ee7b7; }
.status-feil { background: #7f1d1d; color: #fca5a5; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot-gronn { background: #22c55e; animation: puls 2s infinite; }
.dot-rod { background: #ef4444; }
@keyframes puls { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.main { max-width: 900px; margin: 0 auto; padding: 1.5rem; }
.kort {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 0.75rem;
    padding: 1.25rem;
    margin-bottom: 1rem;
}
.kort h2 {
    font-size: 1rem;
    color: #94a3b8;
    margin-bottom: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 500;
}
.info-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0.75rem;
}
.info-boks {
    background: #0f172a;
    border-radius: 0.5rem;
    padding: 0.75rem 1rem;
}
.info-boks .label { font-size: 0.75rem; color: #64748b; }
.info-boks .verdi { font-size: 1.1rem; font-weight: 600; margin-top: 0.25rem; }
.cmd-boks {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 0.5rem;
    padding: 1rem;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 0.85rem;
    color: #a5b4fc;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-top: 0.75rem;
}
.cmd-boks code { flex: 1; word-break: break-all; }
.btn-kopier {
    background: #334155;
    color: #e2e8f0;
    border: none;
    padding: 0.35rem 0.7rem;
    border-radius: 0.25rem;
    cursor: pointer;
    font-size: 0.75rem;
    white-space: nowrap;
}
.btn-kopier:hover { background: #475569; }
.steg {
    counter-reset: steg;
    list-style: none;
    padding: 0;
}
.steg li {
    counter-increment: steg;
    padding: 0.75rem 0 0.75rem 3rem;
    position: relative;
    border-bottom: 1px solid #1e293b;
    font-size: 0.9rem;
    color: #cbd5e1;
}
.steg li:last-child { border-bottom: none; }
.steg li::before {
    content: counter(steg);
    position: absolute;
    left: 0;
    width: 2rem;
    height: 2rem;
    background: #1e1b4b;
    color: #a5b4fc;
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
.tag-aktiv { background: #064e3b; color: #6ee7b7; }
.tag-usb { background: #1e1b4b; color: #a5b4fc; }
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
    border-bottom: 1px solid #0f172a;
    font-size: 0.85rem;
    font-family: 'Consolas', monospace;
    color: #94a3b8;
}
.usb-liste li.sirius { color: #6ee7b7; font-weight: 600; }
.spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid #475569;
    border-top-color: #3b82f6;
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
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 0.5rem;
    padding: 0.6rem 1rem;
    font-family: 'Consolas', monospace;
    font-size: 0.85rem;
    color: #a5b4fc;
    outline: none;
}
.koble-input:focus { border-color: #3b82f6; }
.koble-input::placeholder { color: #475569; }
.btn {
    border: none;
    padding: 0.6rem 1rem;
    border-radius: 0.5rem;
    cursor: pointer;
    font-size: 0.8rem;
    font-weight: 500;
    white-space: nowrap;
}
.btn-gronn { background: #065f46; color: #6ee7b7; }
.btn-gronn:hover { background: #047857; }
.btn-blaa { background: #1e3a5f; color: #93c5fd; }
.btn-blaa:hover { background: #1e40af; }
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
    border-bottom: 1px solid #0f172a;
    font-size: 0.85rem;
}
.enhet-liste .enhet-info {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
}
.enhet-liste .enhet-navn { color: #e2e8f0; font-weight: 500; }
.enhet-liste .enhet-conn { color: #64748b; font-family: 'Consolas', monospace; font-size: 0.75rem; }
.melding {
    margin-top: 0.75rem;
    padding: 0.5rem 0.75rem;
    border-radius: 0.5rem;
    font-size: 0.8rem;
    display: none;
}
.melding-ok { background: #064e3b; color: #6ee7b7; display: block; }
.melding-feil { background: #7f1d1d; color: #fca5a5; display: block; }
.btn-rod { background: #7f1d1d; color: #fca5a5; }
.btn-rod:hover { background: #991b1b; }
.usbip-status-rad {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
    font-size: 0.9rem;
}
.usbip-knapper {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.75rem;
}
.usbip-instruksjoner {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 0.5rem;
    padding: 1rem;
    margin-top: 1rem;
}
.usbip-instruksjoner h3 {
    font-size: 0.85rem;
    color: #94a3b8;
    margin-bottom: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    font-weight: 500;
}
.usbip-instruksjoner ol {
    list-style: decimal;
    padding-left: 1.25rem;
}
.usbip-instruksjoner li {
    font-size: 0.85rem;
    color: #cbd5e1;
    padding: 0.25rem 0;
}
.usbip-instruksjoner code {
    background: #1e293b;
    padding: 0.15rem 0.4rem;
    border-radius: 0.25rem;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 0.8rem;
    color: #a5b4fc;
}
</style>
</head>
<body>

<div class="header">
    <h1>openDAQ Server &mdash; Dewesoft SIRIUS</h1>
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
        <p style="color:#64748b; font-size:0.8rem; margin-top:0.75rem;">
            Pi maaler og lagrer data lokalt, uavhengig av DewesoftX-tilkobling.
            Filer lagres i <code style="color:#a5b4fc;">/data/maalinger/</code>
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
        <p style="color:#64748b; font-size:0.8rem; margin-top:0.75rem;">
            Skriv inn tilkoblingsstreng eller klikk Sok for aa finne enheter paa nettverket.
            Eksempler: <code style="color:#a5b4fc;">daq.opcua://IP</code>,
            <code style="color:#a5b4fc;">daq.ns://IP</code>,
            <code style="color:#a5b4fc;">daqref://device0</code>
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
        <p style="color:#64748b; font-size:0.8rem; margin-top:0.75rem;">
            Klikk OK &mdash; SIRIUS dukker opp under Detected devices.
        </p>
    </div>

    <div class="kort" id="probe-kort">
        <h2>USB Probe &mdash; Direkte SIRIUS-kommunikasjon</h2>
        <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:0.75rem;">
            Test direkte USB-kommunikasjon med SIRIUS via libusb/pyusb.
            Proober FX2 (Cypress) kommandoprotokoll og leser USB-deskriptorer.
        </p>
        <div class="usbip-knapper">
            <button class="btn btn-blaa" id="btn-probe" onclick="kjorProbe()">
                USB Deskriptorer
            </button>
            <button class="btn btn-gronn" id="btn-proto-scan" onclick="kjorProtokoll('scan')">
                Skann kommandoer
            </button>
            <button class="btn btn-gronn" id="btn-proto-stream" onclick="kjorProtokoll('stream')">
                Les datastroemmer
            </button>
            <button class="btn btn-gronn" id="btn-proto-full" onclick="kjorProtokoll('full')">
                Full analyse
            </button>
        </div>
        <div id="probe-status" class="melding" style="display:none;"></div>
        <pre id="probe-output" style="display:none; background:#0f172a; border:1px solid #334155;
             border-radius:0.5rem; padding:1rem; margin-top:0.75rem; font-size:0.75rem;
             color:#a5b4fc; max-height:400px; overflow-y:auto; white-space:pre-wrap;
             font-family:'Consolas','Monaco',monospace;"></pre>
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
                <li>Installer <a href="https://github.com/cezanne/usbip-win2/releases" target="_blank" style="color:#93c5fd;">usbip-win2</a></li>
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
    siriusFunnet.style.color = u.sirius_paa_usb ? '#6ee7b7' : '#fca5a5';

    // Bus-ID
    document.getElementById('usbip-busid').textContent = u.busid || u.sirius_busid_funnet || '-';

    // Deling-status
    const delingEl = document.getElementById('usbip-deling');
    if (u.deling_aktiv) {
        dot.className = 'dot dot-gronn';
        tekst.textContent = 'Deling aktiv paa port 3240';
        delingEl.textContent = 'Aktiv';
        delingEl.style.color = '#6ee7b7';
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
        delingEl.style.color = '#94a3b8';
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
            liste.innerHTML = '<li style="color:#64748b;">Ingen enheter funnet</li>';
        }
    } catch (e) {
        liste.innerHTML = '<li style="color:#fca5a5;">Feil ved sok</li>';
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

async function kjorProtokoll(modus) {
    const btn = document.getElementById('btn-proto-' + modus);
    const statusEl = document.getElementById('probe-status');
    const outputEl = document.getElementById('probe-output');
    const alleBtns = ['btn-probe', 'btn-proto-scan', 'btn-proto-stream', 'btn-proto-full'];
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

hentData();
hentUsbipStatus();
setInterval(hentData, 5000);
setInterval(hentUsbipStatus, 5000);
</script>

</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port)
