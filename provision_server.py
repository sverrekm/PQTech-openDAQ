#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
provision_server — first-boot captive-portal setup for a PQTech-openDAQ node
============================================================================
Runs on the Raspberry Pi HOST (not in the container), started by
pqtech-firstboot.sh while wlan0 is an open access point. Stdlib only — the Pi
host has no pip. Binds :80 on the AP gateway (NetworkManager shared mode gives
10.42.0.1) and serves a small blueprint-styled wizard that collects the node's
basic setup, then writes the config the container reads.

Division of labour (see pqtech-firstboot.sh):
  - This server writes the config FILES directly (.env, konfig/modus.json,
    konfig/push.json) — clean JSON from Python.
  - It then drops a request file (/run/pqtech-provision-request.json) with the
    NETWORK actions (uplink choice, optional Wi-Fi uplink) and returns the done
    page. It must NOT tear down the AP itself: that kills the client's HTTP
    session mid-response. pqtech-firstboot.sh watches for the request file and
    performs the teardown / uplink / `docker compose up` after we have replied.

Secrets (Wi-Fi PSK, tokens) are written to root-only files and never logged.
"""

import json
import os
import re
import secrets
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_DIR = os.environ.get("PQTECH_REPO", os.path.dirname(os.path.abspath(__file__)))
KONFIG_DIR = os.path.join(REPO_DIR, "konfig")
ENV_FIL = os.path.join(REPO_DIR, ".env")
REQUEST_FILE = os.environ.get("PQTECH_REQUEST_FILE", "/run/pqtech-provision-request.json")
PORT = int(os.environ.get("PQTECH_PROVISION_PORT", "80"))

# Captive-portal-probe-URL-ar frå dei ulike OS-a. Alle → redirect til portalen.
CAPTIVE_PROBES = {
    "/generate_204", "/gen_204", "/mobile/status.php", "/ncsi.txt",
    "/connecttest.txt", "/hotspot-detect.html", "/success.txt",
    "/library/test/success.html", "/canonical.html", "/redirect",
}

IPV4 = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


# ---------------------------------------------------------------
#  Vertskommandoar (køyrer som root på hosten — ingen nsenter her)
# ---------------------------------------------------------------
def _run(cmd, timeout=25):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        class _R:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _R()


def skann_wifi():
    """Tilgjengelege WiFi-nett (unike SSID, sterkaste signal)."""
    r = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
              "device", "wifi", "list", "--rescan", "yes"], timeout=30)
    beste = {}
    for ln in (r.stdout or "").splitlines():
        # nmcli -t escaper kolon i verdiar med backslash; SSID kan ha kolon.
        f = re.split(r"(?<!\\):", ln)
        if len(f) < 3:
            continue
        ssid = f[0].replace("\\:", ":").strip()
        if not ssid:
            continue
        try:
            sig = int(f[1])
        except ValueError:
            sig = 0
        sec = f[2].strip()
        n = {"ssid": ssid, "signal": sig, "open": (not sec or sec == "--")}
        if ssid not in beste or sig > beste[ssid]["signal"]:
            beste[ssid] = n
    return sorted(beste.values(), key=lambda x: x["signal"], reverse=True)


def ethernet_status():
    """{iface: carrier_bool} for kabla grensesnitt (eth*/en*/end*)."""
    ut = {}
    base = "/sys/class/net"
    try:
        for ifn in sorted(os.listdir(base)):
            if not re.match(r"^(eth|en|end)", ifn):
                continue
            try:
                with open(os.path.join(base, ifn, "carrier")) as fh:
                    ut[ifn] = fh.read().strip() == "1"
            except OSError:
                ut[ifn] = False
    except OSError:
        pass
    return ut


# ---------------------------------------------------------------
#  Skriv konfig
# ---------------------------------------------------------------
def _sett_env(par):
    """Oppdater/legg til nøkkel=verdi-par i .env (behald resten)."""
    linjer = []
    if os.path.exists(ENV_FIL):
        with open(ENV_FIL, encoding="utf-8") as fh:
            linjer = fh.read().splitlines()
    finst = {}
    for i, ln in enumerate(linjer):
        m = re.match(r"^([A-Z0-9_]+)=", ln)
        if m:
            finst[m.group(1)] = i
    for k, v in par.items():
        ny = f"{k}={v}"
        if k in finst:
            linjer[finst[k]] = ny
        else:
            linjer.append(ny)
    with open(ENV_FIL, "w", encoding="utf-8") as fh:
        fh.write("\n".join(linjer) + "\n")


def _skriv_json(sti, data):
    os.makedirs(os.path.dirname(sti), exist_ok=True)
    tmp = sti + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, sti)
    try:
        os.chmod(sti, 0o600)
    except OSError:
        pass


def bruk_oppsett(d):
    """Skriv all konfig ut frå eit validert wizard-svar. Returnerer (ok, feil)."""
    namn = (d.get("node_namn") or "").strip()
    if not namn:
        return False, "Node name is required."
    rolle = d.get("rolle") if d.get("rolle") in ("node", "hub") else "node"
    ip_mode = d.get("ip_mode") if d.get("ip_mode") in ("auto", "static") else "auto"
    container_ip = (d.get("container_ip") or "").strip()
    if ip_mode == "static" and not IPV4.match(container_ip):
        return False, f"Invalid static IP: {container_ip or '(empty)'}"
    token = (d.get("token") or "").strip()
    hub_url = (d.get("hub_url") or "").strip()

    # .env: IP-modus (+ fast IP for static). Auto => la start.sh velje ledig IP.
    env = {"IP_MODE": ip_mode}
    if ip_mode == "static":
        env["CONTAINER_IP"] = container_ip
        env["OPENDAQ_IP"] = container_ip
    else:
        env["CONTAINER_IP"] = ""
        env["OPENDAQ_IP"] = ""
    if token:
        env["INGEST_TOKEN"] = token
    _sett_env(env)

    # modus.json: rolla (node = 'direkte').
    _skriv_json(os.path.join(KONFIG_DIR, "modus.json"),
                {"modus": "hub" if rolle == "hub" else "direkte"})

    # push.json: identitet + push/ingest. Ein node som pushar til ein hub set
    # parent_url + parent_token; ein hub set accept_ingest + ingest_token.
    push = {"node_namn": namn}
    if rolle == "hub":
        push["accept_ingest"] = True
        if token:
            push["ingest_token"] = token
    else:
        if hub_url:
            push["parent_url"] = hub_url
        if token:
            push["parent_token"] = token
    _skriv_json(os.path.join(KONFIG_DIR, "push.json"), push)

    # Nettverks-handlingane (AP-nedrigging, WiFi-uplink, compose up) må skje
    # ETTER at vi har svara klienten — pqtech-firstboot.sh gjer dei.
    req = {
        "uplink": d.get("uplink") if d.get("uplink") in ("ethernet", "wifi") else "ethernet",
        "wifi_ssid": (d.get("wifi_ssid") or "").strip(),
        "wifi_pass": d.get("wifi_pass") or "",
        "ip_mode": ip_mode,
        "node_namn": namn,
    }
    _skriv_json(REQUEST_FILE, req)
    return True, ""


# ---------------------------------------------------------------
#  Wizard-side (sjølvstendig HTML, blueprint-stil, oransje aksent)
# ---------------------------------------------------------------
SIDE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PQTech openDAQ — Setup</title>
<style>
:root{--accent:#D76428;--ink:#1a1a1a;--line:rgba(26,26,26,.16);--mut:rgba(26,26,26,.6)}
*{box-sizing:border-box}
body{margin:0;background:#f4f2ee;color:var(--ink);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.5}
header{background:#1a1a1a;border-bottom:3px solid var(--accent);padding:16px 20px}
header b{color:#fff;font-size:20px;letter-spacing:.02em}header b span{color:var(--accent)}
main{max-width:640px;margin:0 auto;padding:20px 16px 60px}
.kick{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);font-family:ui-monospace,Menlo,Consolas,monospace}
h1{font-size:26px;margin:2px 0 4px}
p.sub{color:var(--mut);margin:0 0 20px;font-size:14px}
section{border:1px solid var(--line);background:#fff;padding:16px 16px 18px;margin-bottom:16px;position:relative}
section h2{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin:0 0 12px;font-family:ui-monospace,Menlo,Consolas,monospace}
label{display:block;font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);margin:12px 0 4px}
input[type=text],input[type=password],select{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:0;font-size:15px;background:#fff;color:var(--ink);font-family:inherit}
input:focus,select:focus{outline:none;border-color:var(--accent)}
.seg{display:flex;gap:0;border:1px solid var(--line)}
.seg button{flex:1;padding:10px;border:0;background:#fff;color:var(--ink);font:inherit;cursor:pointer;border-right:1px solid var(--line)}
.seg button:last-child{border-right:0}
.seg button[aria-pressed=true]{background:var(--accent);color:#fff}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>*{flex:1;min-width:180px}
.hint{font-size:12px;color:var(--mut);margin-top:6px}
.btn{display:inline-flex;align-items:center;gap:8px;padding:11px 18px;border:1px solid var(--accent);background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer;border-radius:0}
.btn.ghost{background:#fff;color:var(--ink);border-color:var(--line)}
.btn:disabled{opacity:.5;cursor:default}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
[hidden]{display:none!important}
#done{text-align:center;padding:40px 10px}
#done .big{font-size:22px;margin:10px 0}
.err{color:#b3261e;font-size:13px;margin-top:10px}
.sig{font-size:11px;color:var(--mut)}
</style></head><body>
<header><b><span>PQTECH</span> openDAQ</b></header>
<main>
 <div id="form">
  <div class="kick">First-time setup</div>
  <h1>Set up this node</h1>
  <p class="sub">This box is on its setup access point. Fill in the essentials and it will connect, configure itself and start measuring. You can change everything later in the web UI.</p>

  <section>
   <h2>01 — Identity</h2>
   <label>Node name</label>
   <input type="text" id="node_namn" placeholder="e.g. Sundet main board" autocomplete="off">
   <label>Role</label>
   <div class="seg" id="rolle">
     <button type="button" data-v="node" aria-pressed="true">Node — measures</button>
     <button type="button" data-v="hub">Hub — aggregates</button>
   </div>
  </section>

  <section>
   <h2>02 — Uplink</h2>
   <p class="hint">How this box reaches the network/internet. The measurement LAN is the wired port (eth0).</p>
   <div class="seg" id="uplink">
     <button type="button" data-v="ethernet" aria-pressed="true">Ethernet</button>
     <button type="button" data-v="wifi">Wi-Fi</button>
   </div>
   <div id="wifiblock" hidden>
     <label>Wi-Fi network</label>
     <select id="wifi_ssid"><option value="">Scanning…</option></select>
     <label>Wi-Fi password <span class="hint">(blank = open)</span></label>
     <input type="password" id="wifi_pass" autocomplete="off">
   </div>
   <div id="ethinfo" class="hint"></div>
  </section>

  <section>
   <h2>03 — Container IP</h2>
   <p class="hint">The address DewesoftX connects to. Auto is recommended — the node picks a free address on the LAN, so many nodes never collide.</p>
   <div class="seg" id="ip_mode">
     <button type="button" data-v="auto" aria-pressed="true">Auto (pick free)</button>
     <button type="button" data-v="static">Static</button>
   </div>
   <div id="staticblock" hidden>
     <label>Static IP</label>
     <input type="text" id="container_ip" class="mono" placeholder="192.168.1.50">
   </div>
  </section>

  <section>
   <h2>04 — Connection <span class="sig">(optional)</span></h2>
   <div id="hubblock">
     <label>Hub URL <span class="hint">(where this node pushes data)</span></label>
     <input type="text" id="hub_url" class="mono" placeholder="https://opendac.pqtech.no">
   </div>
   <label id="tok_lbl">Token</label>
   <div class="row">
     <input type="text" id="token" class="mono" placeholder="shared node ↔ hub token">
     <button type="button" class="btn ghost" id="gen" style="flex:0 0 auto">Generate</button>
   </div>
  </section>

  <button class="btn" id="submit">Finish setup →</button>
  <div class="err" id="err" hidden></div>
 </div>

 <div id="done" hidden>
  <div class="kick">Setup complete</div>
  <div class="big">This node is configuring itself.</div>
  <p class="sub">The setup Wi-Fi will disappear shortly. Give it a minute, then find the node on your network. You can close this page.</p>
 </div>
</main>
<script>
const $=s=>document.querySelector(s);
const state={rolle:'node',uplink:'ethernet',ip_mode:'auto'};
function seg(id,key,after){document.querySelectorAll('#'+id+' button').forEach(b=>{
  b.onclick=()=>{state[key]=b.dataset.v;
    document.querySelectorAll('#'+id+' button').forEach(x=>x.setAttribute('aria-pressed', x===b));
    after&&after(b.dataset.v);};});}
seg('rolle','rolle',v=>{$('#hubblock').hidden=(v==='hub');
  $('#tok_lbl').textContent=(v==='hub')?'Ingest token (children authenticate with this)':'Token';});
seg('uplink','uplink',v=>{$('#wifiblock').hidden=(v!=='wifi');});
seg('ip_mode','ip_mode',v=>{$('#staticblock').hidden=(v!=='static');});
$('#gen').onclick=()=>{const a=new Uint8Array(18);crypto.getRandomValues(a);
  $('#token').value=btoa(String.fromCharCode(...a)).replace(/[^a-zA-Z0-9]/g,'').slice(0,32);};
fetch('/api/ifaces').then(r=>r.json()).then(d=>{
  const e=Object.entries(d.ethernet||{});
  $('#ethinfo').textContent=e.length? e.map(([k,v])=>k+': '+(v?'cable connected':'no cable')).join(' · '):'';
});
fetch('/api/wifi/scan').then(r=>r.json()).then(d=>{
  const s=$('#wifi_ssid');s.innerHTML='';
  (d.nett||[]).forEach(n=>{const o=document.createElement('option');
    o.value=n.ssid;o.textContent=n.ssid+' ('+n.signal+'%)'+(n.open?' · open':'');s.appendChild(o);});
  if(!s.children.length){const o=document.createElement('option');o.value='';o.textContent='No networks found';s.appendChild(o);}
});
$('#submit').onclick=async()=>{
  $('#err').hidden=true;$('#submit').disabled=true;
  const body={node_namn:$('#node_namn').value,rolle:state.rolle,uplink:state.uplink,
    wifi_ssid:$('#wifi_ssid').value,wifi_pass:$('#wifi_pass').value,ip_mode:state.ip_mode,
    container_ip:$('#container_ip').value,hub_url:$('#hub_url').value,token:$('#token').value};
  try{
    const r=await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json();
    if(j.ok){$('#form').hidden=true;$('#done').hidden=false;}
    else{$('#err').textContent=j.feil||'Setup failed.';$('#err').hidden=false;$('#submit').disabled=false;}
  }catch(e){$('#err').textContent='Could not reach the node. Try again.';$('#err').hidden=false;$('#submit').disabled=false;}
};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PQTechProvision/1.0"

    def log_message(self, *a):  # rolegare logg
        pass

    def _send(self, kode, kropp=b"", ctype="text/html; charset=utf-8", ekstra=None):
        self.send_response(kode)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(kropp)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (ekstra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if kropp:
            self.wfile.write(kropp)

    def _json(self, obj, kode=200):
        self._send(kode, json.dumps(obj).encode("utf-8"),
                   ctype="application/json; charset=utf-8")

    def do_GET(self):
        sti = self.path.split("?", 1)[0]
        if sti in ("/", "/index.html", "/setup"):
            return self._send(200, SIDE.encode("utf-8"))
        if sti == "/api/wifi/scan":
            return self._json({"nett": skann_wifi()})
        if sti == "/api/ifaces":
            return self._json({"ethernet": ethernet_status()})
        # Alt anna (captive-probe eller vilkårleg URL) → send folk til portalen.
        return self._send(302, b"", ekstra={"Location": "http://10.42.0.1/"})

    def do_POST(self):
        sti = self.path.split("?", 1)[0]
        if sti != "/api/submit":
            return self._json({"ok": False, "feil": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            return self._json({"ok": False, "feil": "bad request"}, 400)
        try:
            ok, feil = bruk_oppsett(data)
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "feil": f"internal error: {e}"}, 500)
        return self._json({"ok": ok, "feil": feil})


def main():
    os.makedirs(KONFIG_DIR, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[provision] serverar setup-portal på :{PORT} (repo={REPO_DIR})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
