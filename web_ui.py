#!/usr/bin/env python3
"""
Web-grensesnitt for USB-over-IP (Dewesoft SIRIUS)
==================================================
Flask-app som lar deg se USB-enheter og velge hvilken
som skal deles over nettverket via usbip.

Kjor: python3 web_ui.py
Apne: http://<pi-ip>:8080
"""

import os
import re
import subprocess
import socket

from flask import Flask, jsonify, request

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
    """Finn Pi sin IP-adresse."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        out, _, _ = kjor("hostname -I")
        return out.split()[0] if out else "ukjent"


def hent_usb_enheter():
    """Henter alle USB-enheter med lsusb og usbip list -l."""
    enheter = []

    # lsusb for alle enheter
    out, _, _ = kjor("lsusb")
    for linje in out.splitlines():
        m = re.match(
            r"Bus (\d+) Device (\d+): ID ([0-9a-f:]+)\s+(.*)", linje, re.I
        )
        if m:
            enheter.append({
                "bus": m.group(1),
                "device": m.group(2),
                "id": m.group(3),
                "navn": m.group(4).strip(),
                "busid": "",
                "kan_deles": False,
                "delt": False,
            })

    # usbip list -l for delbare enheter
    usbip_out, _, _ = kjor("usbip list -l")
    current_busid = None
    for linje in usbip_out.splitlines():
        busid_match = re.search(r"busid\s+(\S+)", linje)
        if busid_match:
            current_busid = busid_match.group(1)
            # Koble busid til lsusb-enhet
            for e in enheter:
                if _matcher_busid(e, current_busid):
                    e["busid"] = current_busid
                    e["kan_deles"] = True
                    break
            else:
                # Enhet fra usbip som ikke ble matchet
                enheter.append({
                    "bus": "",
                    "device": "",
                    "id": "",
                    "navn": linje.strip(),
                    "busid": current_busid,
                    "kan_deles": True,
                    "delt": False,
                })

    # Sjekk hvilke som er delt (bundet)
    usbip_bind_out, _, _ = kjor("usbip list -l")
    for linje in usbip_bind_out.splitlines():
        if "usbip-host" in linje.lower():
            # Finn tilhorende busid
            for e in enheter:
                if e["busid"] and e["busid"] in usbip_bind_out:
                    # Sjekk om enheten er bundet
                    bind_out, _, _ = kjor(
                        f"cat /sys/bus/usb/devices/{e['busid']}/usbip_status 2>/dev/null"
                    )
                    if "1" in bind_out:
                        e["delt"] = True

    # Alternativ sjekk: se etter bundet driver
    for e in enheter:
        if e["busid"]:
            driver_out, _, _ = kjor(
                f"readlink /sys/bus/usb/devices/{e['busid']}/driver 2>/dev/null"
            )
            if "usbip-host" in driver_out:
                e["delt"] = True

    return enheter


def _matcher_busid(enhet, busid):
    """Proov aa matche en lsusb-enhet med en usbip busid."""
    # busid er f.eks. "1-1.2", bus er "001"
    bus_nr = busid.split("-")[0] if "-" in busid else ""
    return enhet["bus"].lstrip("0") == bus_nr or bus_nr == enhet["bus"]


def hent_status():
    """Hent usbipd-status."""
    _, _, rc = kjor("pgrep -x usbipd")
    ip = hent_ip()
    return {
        "usbipd_kjorer": rc == 0,
        "ip": ip,
        "port": 3240,
    }


# --- API ---

@app.route("/api/enheter")
def api_enheter():
    return jsonify(hent_usb_enheter())


@app.route("/api/status")
def api_status():
    return jsonify(hent_status())


@app.route("/api/del", methods=["POST"])
def api_del():
    data = request.get_json(force=True)
    busid = data.get("busid", "").strip()
    if not busid or not re.match(r"^[\d\-\.]+$", busid):
        return jsonify({"ok": False, "melding": "Ugyldig busid"}), 400

    # Start usbipd hvis den ikke kjorer
    _, _, rc = kjor("pgrep -x usbipd")
    if rc != 0:
        kjor("usbipd -D")

    out, err, rc = kjor(f"usbip bind -b {busid}")
    if rc == 0:
        return jsonify({"ok": True, "melding": f"Enhet {busid} delt"})
    else:
        return jsonify({"ok": False, "melding": err or out or "Feil ved binding"})


@app.route("/api/stopp", methods=["POST"])
def api_stopp():
    data = request.get_json(force=True)
    busid = data.get("busid", "").strip()
    if not busid or not re.match(r"^[\d\-\.]+$", busid):
        return jsonify({"ok": False, "melding": "Ugyldig busid"}), 400

    out, err, rc = kjor(f"usbip unbind -b {busid}")
    if rc == 0:
        return jsonify({"ok": True, "melding": f"Deling stoppet for {busid}"})
    else:
        return jsonify({"ok": False, "melding": err or out or "Feil ved unbind"})


# --- HTML ---

@app.route("/")
def index():
    return HTML_SIDE


HTML_SIDE = """<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>USB-over-IP - Dewesoft SIRIUS</title>
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
.header h1 {
    font-size: 1.25rem;
    font-weight: 600;
    color: #f8fafc;
}
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
.dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
}
.dot-gronn { background: #22c55e; animation: puls 2s infinite; }
.dot-rod { background: #ef4444; }
@keyframes puls {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
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
table {
    width: 100%;
    border-collapse: collapse;
}
th {
    text-align: left;
    padding: 0.625rem 0.75rem;
    border-bottom: 1px solid #334155;
    color: #64748b;
    font-weight: 500;
    font-size: 0.8rem;
    text-transform: uppercase;
}
td {
    padding: 0.75rem;
    border-bottom: 1px solid #1e293b;
    font-size: 0.9rem;
}
tr:hover td { background: #0f172a; }
.tag {
    display: inline-block;
    padding: 0.2rem 0.5rem;
    border-radius: 0.25rem;
    font-size: 0.75rem;
    font-weight: 500;
}
.tag-delt { background: #064e3b; color: #6ee7b7; }
.tag-ledig { background: #1e293b; color: #64748b; border: 1px solid #334155; }
.tag-usb { background: #1e1b4b; color: #a5b4fc; }
.btn {
    padding: 0.4rem 0.85rem;
    border: none;
    border-radius: 0.375rem;
    font-size: 0.8rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
}
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-del { background: #3b82f6; color: white; }
.btn-del:hover:not(:disabled) { background: #2563eb; }
.btn-stopp { background: #dc2626; color: white; }
.btn-stopp:hover:not(:disabled) { background: #b91c1c; }
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
.melding {
    padding: 0.75rem 1rem;
    border-radius: 0.5rem;
    margin-bottom: 1rem;
    font-size: 0.85rem;
    display: none;
}
.melding-ok { background: #064e3b; color: #6ee7b7; display: block; }
.melding-feil { background: #7f1d1d; color: #fca5a5; display: block; }
.spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid #475569;
    border-top-color: #3b82f6;
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    vertical-align: middle;
    margin-right: 0.3rem;
}
@keyframes spin { to { transform: rotate(360deg); } }
.tom-rad { text-align: center; color: #475569; padding: 2rem !important; }
</style>
</head>
<body>

<div class="header">
    <h1>USB-over-IP &mdash; Dewesoft SIRIUS</h1>
    <div id="status-badge" class="status-badge status-feil">
        <span class="dot dot-rod" id="status-dot"></span>
        <span id="status-tekst">Sjekker...</span>
    </div>
</div>

<div class="main">
    <div id="melding" class="melding"></div>

    <div class="kort">
        <h2>Serverinfo</h2>
        <div class="info-grid">
            <div class="info-boks">
                <div class="label">IP-adresse</div>
                <div class="verdi" id="info-ip">-</div>
            </div>
            <div class="info-boks">
                <div class="label">USB/IP Port</div>
                <div class="verdi" id="info-port">3240</div>
            </div>
            <div class="info-boks">
                <div class="label">usbipd</div>
                <div class="verdi" id="info-daemon">-</div>
            </div>
        </div>
    </div>

    <div class="kort">
        <h2>USB-enheter</h2>
        <table>
            <thead>
                <tr>
                    <th>Bus-ID</th>
                    <th>Enhet</th>
                    <th>Vendor:Product</th>
                    <th>Status</th>
                    <th></th>
                </tr>
            </thead>
            <tbody id="enheter-tabell">
                <tr><td colspan="5" class="tom-rad"><span class="spinner"></span> Laster...</td></tr>
            </tbody>
        </table>
    </div>

    <div class="kort" id="windows-kort" style="display:none;">
        <h2>Koble til fra Windows</h2>
        <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:0.5rem;">
            Kjor denne kommandoen i PowerShell pa Windows-PC:
        </p>
        <div class="cmd-boks">
            <code id="windows-cmd">-</code>
            <button class="btn-kopier" onclick="kopierCmd()">Kopier</button>
        </div>
        <p style="color:#64748b; font-size:0.8rem; margin-top:0.75rem;">
            Krever <a href="https://github.com/vadimgrn/usbip-win2/releases" target="_blank"
            style="color:#60a5fa;">usbip-win2</a> installert pa Windows.
        </p>
    </div>
</div>

<script>
let oppdaterTimer = null;

async function hentData() {
    try {
        const [enheterRes, statusRes] = await Promise.all([
            fetch('/api/enheter'),
            fetch('/api/status')
        ]);
        const enheter = await enheterRes.json();
        const status = await statusRes.json();
        oppdaterUI(enheter, status);
    } catch (e) {
        console.error('Feil ved henting:', e);
    }
}

function oppdaterUI(enheter, status) {
    // Status
    const badge = document.getElementById('status-badge');
    const dot = document.getElementById('status-dot');
    const tekst = document.getElementById('status-tekst');
    if (status.usbipd_kjorer) {
        badge.className = 'status-badge status-ok';
        dot.className = 'dot dot-gronn';
        tekst.textContent = 'Aktiv';
    } else {
        badge.className = 'status-badge status-feil';
        dot.className = 'dot dot-rod';
        tekst.textContent = 'Stoppet';
    }

    document.getElementById('info-ip').textContent = status.ip || '-';
    document.getElementById('info-port').textContent = status.port || '3240';
    document.getElementById('info-daemon').textContent =
        status.usbipd_kjorer ? 'Kjorer' : 'Stoppet';

    // Enheter-tabell
    const tbody = document.getElementById('enheter-tabell');
    const delbare = enheter.filter(e => e.kan_deles);
    const ikkedelbare = enheter.filter(e => !e.kan_deles);

    if (enheter.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="tom-rad">Ingen USB-enheter funnet</td></tr>';
        return;
    }

    let html = '';

    // Delbare enheter forst
    for (const e of delbare) {
        const statusTag = e.delt
            ? '<span class="tag tag-delt">Delt</span>'
            : '<span class="tag tag-ledig">Ledig</span>';
        const knapp = e.delt
            ? `<button class="btn btn-stopp" onclick="stoppDeling('${e.busid}')">Stopp</button>`
            : `<button class="btn btn-del" onclick="delEnhet('${e.busid}')">Del</button>`;
        html += `<tr>
            <td><span class="tag tag-usb">${e.busid || '-'}</span></td>
            <td>${esc(e.navn)}</td>
            <td><code>${esc(e.id)}</code></td>
            <td>${statusTag}</td>
            <td>${knapp}</td>
        </tr>`;
    }

    // Ikke-delbare (hub, etc)
    for (const e of ikkedelbare) {
        html += `<tr style="opacity:0.45;">
            <td>-</td>
            <td>${esc(e.navn)}</td>
            <td><code>${esc(e.id)}</code></td>
            <td><span class="tag tag-ledig">System</span></td>
            <td></td>
        </tr>`;
    }

    tbody.innerHTML = html;

    // Windows-kommando
    const delt = enheter.find(e => e.delt);
    const winKort = document.getElementById('windows-kort');
    if (delt) {
        winKort.style.display = 'block';
        document.getElementById('windows-cmd').textContent =
            `usbip.exe attach -r ${status.ip} -b ${delt.busid}`;
    } else {
        winKort.style.display = 'none';
    }
}

async function delEnhet(busid) {
    visMelding('');
    try {
        const res = await fetch('/api/del', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({busid})
        });
        const data = await res.json();
        visMelding(data.melding, data.ok);
        hentData();
    } catch (e) {
        visMelding('Nettverksfeil', false);
    }
}

async function stoppDeling(busid) {
    visMelding('');
    try {
        const res = await fetch('/api/stopp', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({busid})
        });
        const data = await res.json();
        visMelding(data.melding, data.ok);
        hentData();
    } catch (e) {
        visMelding('Nettverksfeil', false);
    }
}

function visMelding(tekst, ok) {
    const el = document.getElementById('melding');
    if (!tekst) { el.style.display = 'none'; return; }
    el.textContent = tekst;
    el.className = ok ? 'melding melding-ok' : 'melding melding-feil';
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 5000);
}

function kopierCmd() {
    const tekst = document.getElementById('windows-cmd').textContent;
    navigator.clipboard.writeText(tekst).then(() => {
        visMelding('Kommando kopiert!', true);
    });
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

// Start
hentData();
oppdaterTimer = setInterval(hentData, 5000);
</script>

</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port)
