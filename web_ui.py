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

from flask import Flask, jsonify

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

    <div class="kort">
        <h2>USB-enheter paa Pi</h2>
        <ul class="usb-liste" id="usb-liste">
            <li><span class="spinner"></span> Laster...</li>
        </ul>
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

    // USB-enheter
    const liste = document.getElementById('usb-liste');
    if (s.usb_enheter.length > 0) {
        liste.innerHTML = s.usb_enheter.map(u => {
            const erSirius = /sirius|dewesoft|dewetron/i.test(u);
            return `<li class="${erSirius ? 'sirius' : ''}">${esc(u)}</li>`;
        }).join('');
    } else {
        liste.innerHTML = '<li>Ingen USB-enheter funnet</li>';
    }
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

hentData();
setInterval(hentData, 5000);
</script>

</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port)
