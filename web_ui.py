#!/usr/bin/env python3
"""
Web-grensesnitt for PQTech openDAQ Server
==========================================
Flask-app som viser openDAQ server-status og gir
instruksjoner for tilkobling fra DewesoftX.

Kjor: python3 web_ui.py
Apne: http://<pi-ip>:8080
"""

import os
import re
import subprocess
import socket
import threading
import glob as glob_mod

import requests as _http_proxy
from flask import (Flask, jsonify, request, session, send_file, redirect, Response,
                   g, stream_with_context)

import usbip_manager
import tailscale_manager
import oppdatering
import influx_pusher
import emc_pusher
import hub_lager
import brukar_auth
import api_nokkel

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
        gjenoppliv_ep2 as _sirius_gjenoppliv_ep2,
        hent_opendaq_status as _opendaq_hent_status,
        restart_opendaq_bro as _opendaq_restart,
        hent_opendaq_verdiar as _opendaq_hent_verdiar,
        hent_mqtt_status as _mqtt_hent_status,
        hent_mqtt_konfig_dict as _mqtt_hent_konfig,
        oppdater_mqtt as _mqtt_oppdater,
        hent_enhet_konfig_dict as _enhet_hent_konfig,
        oppdater_enhet as _enhet_oppdater,
        hent_buffer_status as _buffer_hent_status,
        hent_buffer_data as _buffer_hent_data,
        marker_buffer_synkronisert as _buffer_marker_synk,
        hent_buffer_konfig_dict as _buffer_hent_konfig,
        oppdater_buffer as _buffer_oppdater,
        hent_buffer_hendingar as _buffer_hent_hendingar,
        hent_buffer_mqtt_logg as _buffer_hent_mqtt_logg,
        hent_buffer_lagringsinfo as _buffer_hent_lagring,
        tom_buffer as _buffer_tom,
        oppdater_modbus_konfig_og_restart as _modbus_restart_etter_konfig,
        hent_modbus_nodar as _sirius_hent_modbus_nodar,
        hent_modbus_kanalar as _sirius_hent_modbus_kanalar,
        hent_raw_vindu as _sirius_hent_raw_vindu,
    )
    SIRIUS_DIREKTE = True
except ImportError:
    SIRIUS_DIREKTE = False

from kanal_konfig import KanalKonfig, les_konfig, lagre_konfig, valider_konfig, STANDARD_KONFIG
from mqtt_konfig import valider_mqtt_konfig
from enhet_konfig import valider_enhet_konfig, les_modus, lagre_modus, MODUS_DIREKTE, MODUS_USBIP, MODUS_HUB
from buffer_konfig import valider_buffer_konfig, les_buffer_konfig
from push_konfig import (
    valider_push_konfig, les_push_konfig, lagre_push_konfig, PushKonfig,
)

# Globalt register over mottatte push-batchar (RAM, ringbuffer-basert).
# Berre dei siste N batchar per node held vi i minne for live-visning.
# Vidare lagring/openDAQ-injeksjon kjem dag 2.
import collections
_INGEST_BUFFER_PER_NODE = 200
_ingest_data: dict = collections.defaultdict(
    lambda: collections.deque(maxlen=_INGEST_BUFFER_PER_NODE))
_ingest_lock = threading.Lock()
_ingest_stats = {"totalt": 0, "avvist": 0, "siste_ts": 0.0}

# Hub-konfig er alltid tilgjengeleg (for GUI), men hub_server berre i hub-modus
HUB_MODUS = os.environ.get("OPENDAQ_MODUS") == "hub"
from hub_konfig import (
    les_hub_konfig, lagre_hub_konfig, valider_hub_konfig, HubKonfig, FjernNode,
)
if HUB_MODUS:
    from hub_server import (
        hent_hub_status, hent_hub_konfig_dict,
        oppdater_hub_konfig, legg_til_node_api,
        fjern_node_api, rekoble_node, hent_logg as _hub_hent_logg,
        hent_hub_kanalar, hent_hub_buffer_status,
        hent_kanal_ranges_dict, oppdater_kanal_ranges,
        restart_hub as _hub_restart,
    )

app = Flask(__name__)
brukar_auth.init_app(app)

# Hub og node køyrer same image → begge ville elles brukt Flask sin
# standard session-cookie "session" på same domene (opendac.pqtech.no).
# Når hubben reverse-proxyar node si web-UI, set noden sin "session"-cookie
# som då overskriv hub-sesjonen → neste /node-proxy-kall ser brukar som
# utlogga og redirectar til "/" (= tilbake til hubben). Gi hubben eit eige
# cookie-namn så node- og hub-sesjon kan eksistere side om side.
if HUB_MODUS:
    app.config["SESSION_COOKIE_NAME"] = "hubsession"


# --- Auth API ---

@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    brukarnavn = data.get("brukarnavn", "")
    passord = data.get("passord", "")
    if brukar_auth.sjekk_passord(brukarnavn, passord):
        session["brukar"] = brukarnavn
        return jsonify({"suksess": True})
    return jsonify({"suksess": False, "melding": "Feil brukarnavn eller passord"}), 401


@app.route("/api/auth/status")
def api_auth_status():
    if "brukar" in session:
        return jsonify({"innlogga": True, "brukarnavn": session["brukar"]})
    return jsonify({"innlogga": False})


@app.route("/api/auth/endre-passord", methods=["POST"])
def api_auth_endre_passord():
    data = request.get_json(silent=True) or {}
    brukarnavn = session.get("brukar", "")
    ok, melding = brukar_auth.endre_passord(
        brukarnavn, data.get("gammalt", ""), data.get("nytt", "")
    )
    return jsonify({"suksess": ok, "melding": melding})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.pop("brukar", None)
    return jsonify({"suksess": True})


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


# --- Node-proxy: hub forwardar HTTP til ein remote node si web-UI ---
#
# Bruksmåte: brukar opnar https://opendac.pqtech.no/node-proxy/<node_id>/
# Hub-session må vere aktiv. Hub forwardar HTTP-call til
# http://<node-tailscale-ip>:8080/... over Tailscale. Browser ser proxy-URL,
# node ser hub som klient.
#
# Node har eigen login — brukar må logge inn på node sin web-UI separat
# (cookies blir forwarded så det held over reload).

_NODE_PROXY_PORT = int(os.environ.get("NODE_PROXY_PORT", "8080"))
_NODE_PROXY_HOP_HEADERS = {
    "content-length", "content-encoding", "transfer-encoding",
    "connection", "keep-alive", "upgrade", "proxy-authorization",
    "proxy-authenticate", "te", "trailers",
}


def _hent_node_for_proxy(node_id: str):
    """Finn fjern-node frå hub-konfig. Returnerer None hvis ikkje funne."""
    try:
        konfig = les_hub_konfig()
    except Exception:
        return None
    for n in konfig.nodar:
        if n.id == node_id:
            return n
    return None


def _node_utilgjengeleg_html(node) -> str:
    """Lesbar feilside når ein node ikkje svarar (i staden for blank skjerm)."""
    namn = getattr(node, "namn", "") or "Noden"
    adresse = getattr(node, "adresse", "")
    return (
        "<!DOCTYPE html><html lang=\"no\"><head><meta charset=\"utf-8\">"
        "<title>Node utilgjengeleg</title><style>"
        "body{font-family:system-ui,sans-serif;background:#f7f7f8;color:#333;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
        ".k{background:#fff;border:1px solid #e5e5e5;border-radius:12px;padding:32px;"
        "max-width:420px;box-shadow:0 1px 3px rgba(0,0,0,.06)}"
        "h1{font-size:18px;margin:0 0 8px}p{font-size:14px;line-height:1.5;color:#666;margin:0 0 16px}"
        "code{background:#f3f3f3;padding:2px 6px;border-radius:4px;font-size:13px}"
        "a{color:#D76428;text-decoration:none;font-size:14px}"
        "</style></head><body><div class=\"k\">"
        f"<h1>{namn} svarar ikkje</h1>"
        f"<p>Hubben nådde ikkje <code>{adresse}</code>. Noden kan vere avslått, "
        "utan nett, eller flytta til ei ny adresse.</p>"
        "<a href=\"/\">&larr; Tilbake til hubben</a>"
        "</div></body></html>"
    )


@app.route("/node-proxy/<node_id>/", defaults={"sub_path": ""},
           methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@app.route("/node-proxy/<node_id>/<path:sub_path>",
           methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def node_proxy(node_id, sub_path):
    """Reverse-proxy til node si web-UI.

    Hub-session krevst for å nå denne ruta. Trafikken blir forwarda til
    http://<node-adresse>:8080/<sub_path> via Tailscale.
    """
    if "brukar" not in session:
        # API/XHR-kall (og alt som ikkje er GET) toler ikkje ein HTML-redirect
        # til "/": fetch følgjer den og endar i 405/HTML → frontenden viser
        # "Could not contact the server" ved t.d. innlogging på noden. Svar
        # heller 401 JSON så frontenden handterer det (re-login på hubben).
        # Topp-nivå GET-navigasjon (sidelasting) får framleis redirect til login.
        if "/api/" in request.path or request.method != "GET":
            return jsonify({"feil": "Hub-session utløpt — logg inn på hubben på nytt."}), 401
        return redirect("/")

    node = _hent_node_for_proxy(node_id)
    if node is None:
        return jsonify({"feil": f"Node {node_id} finst ikkje i hub-konfig"}), 404

    # Bruk berre IP-delen av node.adresse (uten port)
    node_host = str(node.adresse).split(":")[0].strip()
    if not node_host:
        return jsonify({"feil": "Node manglar IP-adresse"}), 502

    target_url = f"http://{node_host}:{_NODE_PROXY_PORT}/{sub_path}"

    # Bygg forward-request
    # Referer vert med vilje IKKJE forwarda: han peikar på /node-proxy/<id>/,
    # som berre gir meining på hubben. Noden køyrer same image (og dermed same
    # _proxy_assets_rewrite), så ein forwarda Referer får noden til å tru at
    # HAN er proxyen og redirecte på nytt → dobbelt prefiks → 404 på alle
    # API-kall frå node-UI-et (kvit side).
    fwd_headers = {k: v for k, v in request.headers
                   if k.lower() not in _NODE_PROXY_HOP_HEADERS
                   and k.lower() not in ("host", "referer")}
    # Set X-Forwarded-* slik at noden veit kva proxy-prefiks å bruke
    fwd_headers["X-Forwarded-Host"] = request.host
    fwd_headers["X-Forwarded-Proto"] = request.scheme
    fwd_headers["X-Forwarded-Prefix"] = f"/node-proxy/{node_id}"
    # Single sign-on: brukaren er alt autentisert på hubben (sjekka over).
    # Signer kallet med delt flåte-token så noden stolar på det og slepp
    # brukaren rett inn utan eige node-login. Token blir aldri sendt til
    # browseren — berre hub→node over Tailscale.
    _sso_tok = _floate_token()
    if _sso_tok:
        fwd_headers["X-Hub-Auth"] = _sso_tok

    try:
        upstream = _http_proxy.request(
            method=request.method,
            url=target_url,
            headers=fwd_headers,
            data=request.get_data(),
            cookies=request.cookies,
            params=request.query_string,
            allow_redirects=False,
            stream=True,
            # (connect, read): ein node som er heilt nede skal feile raskt.
            # 30 s connect-timeout gav 30 sekund blank side i nettlesaren før
            # feilmeldinga kom.
            timeout=(4.0, 30.0),
        )
    except _http_proxy.exceptions.RequestException as e:
        if "text/html" in request.headers.get("Accept", ""):
            # Toppnivå-navigasjon: vis noko lesbart, ikkje ein JSON-klump.
            return Response(_node_utilgjengeleg_html(node), status=502,
                            mimetype="text/html")
        return jsonify({"feil": f"Node ikkje nåbar: {e}"}), 502

    # Bygg respons med headers (filtrer bort hop-by-hop)
    resp_headers = []
    for k, v in upstream.raw.headers.items():
        if k.lower() in _NODE_PROXY_HOP_HEADERS:
            continue
        # Rewrite Location-headers så redirect held seg innanfor proxy
        if k.lower() == "location" and v.startswith("/"):
            v = f"/node-proxy/{node_id}{v}"
        resp_headers.append((k, v))

    return Response(upstream.content, status=upstream.status_code,
                    headers=resp_headers)


@app.before_request
def _proxy_assets_rewrite():
    """Node sin SPA hentar /assets/*, /api/* og favicon som absolutte
    rot-stiar. Når browseren er inni ein node-proxy (Referer peikar på
    /node-proxy/<id>/), redirectar vi desse til riktig /node-proxy/<id>-
    prefiks slik at dei treffer noden — og IKKJE hubben sin eigen frontend
    eller API. Utan dette lastar node-sida, men alle data-kall fell tilbake
    på hubben → brukar ser hub-data inni node-sida.

    307 (ikkje 302) vert brukt slik at HTTP-metode og body held seg, viktig
    for POST/PUT mot /api/* (t.d. innlogging på noden).

    Server-til-server-kall (node→hub /api/ingest, hub→node buffer-pull) har
    ingen node-proxy-Referer og blir difor aldri redirecta.
    """
    if request.path.startswith("/node-proxy/"):
        return None
    # Kjem requesten frå hub-proxyen, er prefikset alt handtert der. Utan denne
    # sperra ville noden (same image, same rewrite) redirecte ein forwarda
    # request på nytt og byggje opp dobbelt prefiks.
    if request.headers.get("X-Forwarded-Prefix"):
        return None
    if not (request.path.startswith("/assets/")
            or request.path.startswith("/api/")
            or request.path in ("/favicon.svg", "/favicon.ico", "/vite.svg")):
        return None
    referer = request.headers.get("Referer", "")
    m = re.search(r"/node-proxy/([A-Za-z0-9_-]+)/", referer)
    if not m:
        return None
    mål = f"/node-proxy/{m.group(1)}{request.path}"
    if request.query_string:
        mål += "?" + request.query_string.decode("latin-1")
    return redirect(mål, code=307)


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


@app.route("/api/sirius/gjenoppliv-ep2", methods=["POST"])
def api_sirius_gjenoppliv_ep2():
    """Forsøk å gjenopplive EP2 via kommando-strategiar."""
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False, "melding": "Driver ikke lastet"}), 503
    try:
        suksess, melding = _sirius_gjenoppliv_ep2()
        return jsonify({"suksess": suksess, "melding": melding})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/sirius/ep2-strategi")
def api_ep2_strategi():
    """EP2-strategi historikk og statistikk."""
    if not SIRIUS_DIREKTE:
        return jsonify({"feil": "Driver ikke lastet"}), 503
    try:
        status = _sirius_hent_status()
        return jsonify(status.get("ep2_strategi", {}))
    except Exception as e:
        return jsonify({"feil": str(e)}), 500


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


@app.route("/api/opendaq/restart", methods=["POST"])
def api_opendaq_restart():
    """Restart openDAQ bridge manuelt."""
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False, "melding": "SIRIUS-driver ikkje lasta"}), 503
    try:
        ok, melding = _opendaq_restart()
        return jsonify({"suksess": ok, "melding": melding})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/opendaq/verdiar")
def api_opendaq_verdiar():
    """Siste kanal-verdiar frå openDAQ bridge (live-visning)."""
    if not SIRIUS_DIREKTE:
        return jsonify({})
    try:
        return jsonify(_opendaq_hent_verdiar())
    except Exception as e:
        return jsonify({"feil": str(e)})


# --- Kanal-konfigurasjon API ---

@app.route("/api/kanalar")
def api_kanalar_hent():
    """Hent gjeldande kanal-konfigurasjon."""
    konfig = les_konfig()
    return jsonify([k.til_dict() for k in konfig])


@app.route("/api/kanalar", methods=["PUT"])
def api_kanalar_oppdater():
    """Oppdater kanal-konfigurasjon."""
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"suksess": False, "melding": "Ugyldig JSON"}), 400

    konfig, feil = valider_konfig(data)
    if feil:
        return jsonify({"suksess": False, "melding": feil}), 400

    ok = lagre_konfig(konfig)
    if ok:
        # Restart openDAQ-broen slik at nye skaleringsfaktorar vert brukte
        try:
            _opendaq_restart()
        except Exception as e:
            log.warning(f"Bridge restart etter konfig-endring feila: {e}")
        return jsonify({"suksess": True, "melding": "Konfigurasjon lagra — bridge restartar"})
    return jsonify({"suksess": False, "melding": "Kunne ikkje lagre konfigurasjon"}), 500


@app.route("/api/kanalar/reset", methods=["POST"])
def api_kanalar_reset():
    """Tilbakestill til standard kanal-konfigurasjon."""
    from dataclasses import asdict
    standard = [KanalKonfig(**asdict(k)) for k in STANDARD_KONFIG]
    ok = lagre_konfig(standard)
    if ok:
        return jsonify({"suksess": True, "melding": "Tilbakestilt til standard"})
    return jsonify({"suksess": False, "melding": "Kunne ikkje tilbakestille"}), 500


@app.route("/api/kanalar/live")
def api_kanalar_live():
    """Hent live kanal-verdiar (frå openDAQ + driver)."""
    resultat = {}

    # openDAQ verdiar (oppdatert via data-callback)
    if SIRIUS_DIREKTE:
        try:
            resultat["opendaq"] = _opendaq_hent_verdiar()
        except Exception:
            resultat["opendaq"] = {}

        # Driver siste data snapshot
        try:
            resultat["driver"] = _sirius_hent_data()
        except Exception:
            resultat["driver"] = {}

    return jsonify(resultat)


# --- MQTT API ---

@app.route("/api/mqtt/konfig")
def api_mqtt_konfig_hent():
    """Hent gjeldande MQTT-konfigurasjon."""
    if not SIRIUS_DIREKTE:
        return jsonify({"broker": {}, "kanalar": []})
    try:
        return jsonify(_mqtt_hent_konfig())
    except Exception as e:
        return jsonify({"feil": str(e)}), 500


@app.route("/api/mqtt/konfig", methods=["PUT"])
def api_mqtt_konfig_oppdater():
    """Oppdater MQTT-konfigurasjon (broker + kanalar)."""
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False, "melding": "SIRIUS-driver ikkje lasta"}), 503
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"suksess": False, "melding": "Ugyldig JSON"}), 400

    konfig, feil = valider_mqtt_konfig(data)
    if feil:
        return jsonify({"suksess": False, "melding": feil}), 400

    try:
        ok, melding = _mqtt_oppdater(konfig)
        return jsonify({"suksess": ok, "melding": melding})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/mqtt/status")
def api_mqtt_status():
    """Hent MQTT-klient tilkoblingsstatus og siste verdiar."""
    if not SIRIUS_DIREKTE:
        return jsonify({"tilkobla": False, "aktivert": False})
    try:
        return jsonify(_mqtt_hent_status())
    except Exception as e:
        return jsonify({"feil": str(e)}), 500


# --- Enheit-konfig API ---

@app.route("/api/enhet/konfig")
def api_enhet_konfig():
    """Hent enheit-konfig (antal ADC-kanalar, modell)."""
    if not SIRIUS_DIREKTE:
        return jsonify({"antal_adc_kanalar": 8, "modell": ""})
    try:
        return jsonify(_enhet_hent_konfig())
    except Exception as e:
        return jsonify({"feil": str(e)}), 500


@app.route("/api/enhet/konfig", methods=["PUT"])
def api_enhet_konfig_oppdater():
    """Oppdater enheit-konfig."""
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False, "melding": "Ikkje SIRIUS-modus"}), 400
    data = request.get_json()
    if not data:
        return jsonify({"suksess": False, "melding": "Tomt request body"}), 400
    konfig, feil = valider_enhet_konfig(data)
    if feil:
        return jsonify({"suksess": False, "melding": feil}), 400
    suksess, melding = _enhet_oppdater(konfig)
    return jsonify({"suksess": suksess, "melding": melding})


# --- Modus API ---

@app.route("/api/modus")
def api_modus():
    """Returnerer gjeldande driftsmodus (direkte/usbip/hub)."""
    modus = "hub" if HUB_MODUS else les_modus()
    return jsonify({"modus": modus, "hub_modus": HUB_MODUS})


@app.route("/api/modus/bytt", methods=["POST"])
def api_modus_bytt():
    """Byt driftsmodus (hub/direkte). Gjenstartar containeren."""
    data = request.get_json(silent=True) or {}
    ny_modus = data.get("modus", "").strip()
    if ny_modus not in (MODUS_HUB, MODUS_DIREKTE):
        return jsonify({"suksess": False, "melding": "Ugyldig modus"}), 400
    lagre_modus(ny_modus)
    threading.Timer(2.0, lambda: os._exit(0)).start()
    return jsonify({"suksess": True, "melding": f"Byter til {ny_modus}-modus — gjenstartar..."})


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
    if suksess:
        lagre_modus(MODUS_USBIP)
    status = usbip_manager.hent_usbip_status()
    return jsonify({"suksess": suksess, "melding": melding, "status": status})


@app.route("/api/usbip/stopp", methods=["POST"])
def api_usbip_stopp():
    """Stopp USB-deling (unbind) og rekoblar native driver."""
    suksess, melding = usbip_manager.stopp_deling()
    if suksess:
        lagre_modus(MODUS_DIREKTE)

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


# --- Tailscale API ---

@app.route("/api/tailscale/status")
def api_tailscale_status():
    """Returnerer Tailscale VPN-status."""
    return jsonify(tailscale_manager.hent_status())


@app.route("/api/tailscale/start", methods=["POST"])
def api_tailscale_start():
    """Start Tailscale VPN."""
    data = request.get_json(silent=True) or {}
    authkey = data.get("authkey", "").strip()
    if not authkey:
        return jsonify({"suksess": False, "melding": "Auth key manglar"}), 400
    hostname = data.get("hostname", "").strip() or None
    suksess, melding = tailscale_manager.start(authkey, hostname)
    status = tailscale_manager.hent_status()
    return jsonify({"suksess": suksess, "melding": melding, "status": status})


@app.route("/api/tailscale/installer", methods=["POST"])
def api_tailscale_installer():
    """Installer Tailscale i containeren."""
    suksess, melding = tailscale_manager.installer()
    status = tailscale_manager.hent_status()
    return jsonify({"suksess": suksess, "melding": melding, "status": status})


@app.route("/api/tailscale/avinstaller", methods=["POST"])
def api_tailscale_avinstaller():
    """Avinstaller Tailscale frå containeren."""
    suksess, melding = tailscale_manager.avinstaller()
    status = tailscale_manager.hent_status()
    return jsonify({"suksess": suksess, "melding": melding, "status": status})


@app.route("/api/tailscale/stopp", methods=["POST"])
def api_tailscale_stopp():
    """Stopp Tailscale VPN."""
    suksess, melding = tailscale_manager.stopp()
    status = tailscale_manager.hent_status()
    return jsonify({"suksess": suksess, "melding": melding, "status": status})


# --- Hub API (tilgjengeleg i alle moduser) ---
# I node-modus: konfig les/skriv direkte frå fil, status viser "ikkje aktiv"
# I hub-modus: delegerer til hub_server som har live tilkoblingar

def _hent_ip_intern():
    """Finn maskinens IP-adresse."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "ukjent"


@app.route("/api/hub/status")
def api_hub_status():
    """Hub-status med per-node info.

    I direkte-modus: modbus-nodar har live tilkobling-status frå ModbusManager,
    openDAQ-nodar er ikkje aktive (hub aggregerer dei).
    """
    if HUB_MODUS:
        return jsonify(hent_hub_status())

    konfig = les_hub_konfig()

    # Hent modbus-status frå sirius_server sin ModbusManager (viss tilgjengeleg)
    modbus_status = {}
    if SIRIUS_DIREKTE:
        try:
            mb_nodar = _sirius_hent_modbus_nodar()
            for n in mb_nodar:
                modbus_status[n["id"]] = n
        except Exception:
            pass

    nodar_info = []
    tilkobla_count = 0
    total_kanalar = 0
    for node in konfig.nodar:
        mb = modbus_status.get(node.id, {})
        er_modbus = node.type == "modbus_tcp"
        tilkobla = mb.get("tilkobla", False) if er_modbus else False
        antal_kanalar = len(node.modbus_registers) if er_modbus else 0
        if tilkobla:
            tilkobla_count += 1
        total_kanalar += antal_kanalar
        nodar_info.append({
            "id": node.id,
            "namn": node.namn,
            "adresse": node.adresse,
            "port": node.port,
            "protokoll": node.protokoll,
            "lokasjon": node.lokasjon,
            "aktivert": node.aktivert,
            "type": node.type,
            "modbus_unit_id": node.modbus_unit_id,
            "modbus_poll_hz": node.modbus_poll_hz,
            "modbus_timeout_ms": node.modbus_timeout_ms,
            "modbus_registers": [r.til_dict() for r in node.modbus_registers],
            "tilkobla": tilkobla,
            "feil": mb.get("feil") if er_modbus else None,
            "sist_sett": mb.get("sist_sett") if er_modbus else None,
            "tilkobla_sidan": mb.get("tilkobla_sidan") if er_modbus else None,
            "antal_kanalar": antal_kanalar,
        })
    return jsonify({
        "modus": "node",
        "aktiv": True if SIRIUS_DIREKTE else False,
        "startet": None,
        "totalt_kanalar": total_kanalar,
        "totalt_nodar": len(nodar_info),
        "tilkobla_nodar": tilkobla_count,
        "nodar": nodar_info,
        "ip": _hent_ip_intern(),
    })


@app.route("/api/hub/konfig")
def api_hub_konfig_hent():
    """Hent hub-konfigurasjon."""
    if HUB_MODUS:
        return jsonify(hent_hub_konfig_dict())
    konfig = les_hub_konfig()
    return jsonify(konfig.til_dict())


@app.route("/api/hub/konfig", methods=["PUT"])
def api_hub_konfig_oppdater():
    """Oppdater hub-konfigurasjon."""
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"suksess": False, "melding": "Ugyldig JSON"}), 400
    konfig, feil = valider_hub_konfig(data)
    if feil:
        return jsonify({"suksess": False, "melding": feil}), 400
    if HUB_MODUS:
        ok, melding = oppdater_hub_konfig(konfig)
        return jsonify({"suksess": ok, "melding": melding})
    # Node-modus: lagre til fil + trigg modbus-restart viss SIRIUS køyrer
    ok = lagre_hub_konfig(konfig)
    melding_base = "Konfig lagra" if ok else "Kunne ikkje lagre konfig"
    if ok and SIRIUS_DIREKTE:
        try:
            _modbus_restart_etter_konfig()
            melding_base = "Konfig lagra — ModbusManager + openDAQ-bru restartar"
        except Exception as e:
            melding_base = f"Konfig lagra, men restart feila: {e}"
    return jsonify({"suksess": ok, "melding": melding_base})


@app.route("/api/hub/nodar", methods=["POST"])
def api_hub_legg_til_node():
    """Legg til ein ny fjern-node (openDAQ eller modbus_tcp)."""
    data = request.get_json(silent=True) or {}
    if HUB_MODUS:
        ok, melding, node = legg_til_node_api(data)
        result = {"suksess": ok, "melding": melding}
        if node:
            result["node"] = node
        return jsonify(result), 200 if ok else 400

    # Node-modus: valider via hub_konfig-validator (støttar både openDAQ + modbus)
    import uuid as _uuid
    if not isinstance(data, dict):
        return jsonify({"suksess": False, "melding": "Forventa objekt"}), 400
    node_dict = dict(data)
    if "id" not in node_dict:
        node_dict["id"] = _uuid.uuid4().hex[:8]
    if not str(node_dict.get("adresse", "")).strip():
        return jsonify({"suksess": False, "melding": "Mangler 'adresse'"}), 400

    validert, feil = valider_hub_konfig({"nodar": [node_dict]})
    if feil:
        return jsonify({"suksess": False, "melding": feil}), 400
    ny_node = validert.nodar[0]

    konfig = les_hub_konfig()
    konfig.nodar.append(ny_node)
    ok = lagre_hub_konfig(konfig)

    melding = f"Node '{ny_node.namn}' lagt til"
    if ok and SIRIUS_DIREKTE:
        try:
            _modbus_restart_etter_konfig()
            melding += " — openDAQ-bru restartar"
        except Exception as e:
            melding += f" (restart feila: {e})"

    return jsonify({
        "suksess": ok,
        "melding": melding if ok else "Lagring feila",
        "node": ny_node.til_dict() if ok else None,
    })


@app.route("/api/hub/nodar/<node_id>", methods=["DELETE"])
def api_hub_fjern_node(node_id):
    """Fjern ein fjern-node."""
    if HUB_MODUS:
        ok, melding = fjern_node_api(node_id)
        return jsonify({"suksess": ok, "melding": melding}), 200 if ok else 404
    # Node-modus: fjern frå konfig-fil
    konfig = les_hub_konfig()
    node = next((n for n in konfig.nodar if n.id == node_id), None)
    if not node:
        return jsonify({"suksess": False, "melding": f"Node '{node_id}' ikkje funnen"}), 404
    konfig.nodar = [n for n in konfig.nodar if n.id != node_id]
    ok = lagre_hub_konfig(konfig)
    melding = f"Node '{node.namn}' fjerna"
    if ok and SIRIUS_DIREKTE:
        try:
            _modbus_restart_etter_konfig()
        except Exception:
            pass
    return jsonify({"suksess": ok, "melding": melding})


@app.route("/api/hub/nodar/<node_id>/rekoble", methods=["POST"])
def api_hub_rekoble_node(node_id):
    """Tving rekobling av ein node."""
    if HUB_MODUS:
        ok, melding = rekoble_node(node_id)
        return jsonify({"suksess": ok, "melding": melding})
    # Node-modus: trig restart av modbus-manageren viss det er ein modbus-node
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False,
                        "melding": "Hub ikkje aktiv og SIRIUS ikkje tilgjengeleg"})
    try:
        _modbus_restart_etter_konfig()
        return jsonify({"suksess": True, "melding": "Modbus-nodar rekoplar"})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)})


@app.route("/api/hub/restart", methods=["POST"])
def api_hub_restart():
    """Restart hub-prosessen for å aktivere node-konfig-endringar.

    Berre i hub-modus — direkte-modus restartar openDAQ-brua automatisk
    ved konfig-endring.
    """
    if not HUB_MODUS:
        return jsonify({"suksess": False,
                        "melding": "Berre tilgjengeleg i hub-modus"}), 400
    ok, melding = _hub_restart()
    return jsonify({"suksess": ok, "melding": melding})


@app.route("/api/hub/kanalar")
def api_hub_kanalar():
    """Kanal-metadata og live-verdiar frå tilkobla nodar.

    Hub-modus: openDAQ-nodar + modbus-nodar.
    Direkte-modus: berre modbus-nodar (openDAQ-aggregering er hub-funksjonalitet).
    """
    if HUB_MODUS:
        try:
            return jsonify({"kanalar": hent_hub_kanalar()})
        except Exception as e:
            return jsonify({"kanalar": [], "feil": str(e)})
    # Direkte-modus: returner modbus-kanalar via sirius_server si helper
    if SIRIUS_DIREKTE:
        try:
            return jsonify({"kanalar": _sirius_hent_modbus_kanalar()})
        except Exception as e:
            return jsonify({"kanalar": [], "feil": str(e)})
    return jsonify({"kanalar": []})


def _lokale_kanalar_for_eksport():
    """Lokale SIRIUS/openDAQ-kanalar (direkte-modus) som HubKanal-liknande
    dicts, slik at dei kan delast til Influx/Prometheus saman med modbus."""
    ut = []
    try:
        kfg = les_konfig()
    except Exception:
        return ut
    try:
        odaq = _opendaq_hent_verdiar()
    except Exception:
        odaq = {}
    try:
        drv = _sirius_hent_data()
    except Exception:
        drv = {}
    nodenamn = socket.gethostname()
    for k in kfg:
        if not getattr(k, "aktiv", True):
            continue
        key = f"kanal_{k.indeks}"
        o = odaq.get(key) or {}
        d = drv.get(key) or {}
        verdi = o.get("siste")
        if verdi is None:
            verdi = d.get("siste")
        ut.append({
            "node_namn": nodenamn, "node_id": nodenamn,
            "namn": k.namn, "verdi": verdi, "eining": getattr(k, "enhet", ""),
            "kanal_type": "opendaq", "tilkobla": True,
        })
    return ut


def _hent_kanalar_for_eksport():
    """Kanal-liste for eksport (Prometheus/Influx): hub-modus aggregerer alle
    nodar; direkte-modus gir lokale SIRIUS-kanalar + modbus-kanalar."""
    if HUB_MODUS:
        return hent_hub_kanalar()
    if SIRIUS_DIREKTE:
        return _lokale_kanalar_for_eksport() + _sirius_hent_modbus_kanalar()
    return []


@app.route("/api/metrics")
def api_metrics():
    """Prometheus-eksponering av kanalverdiane for Grafana-deling.

    Token-verna (hubben er offentleg eksponert): send token som
    `?token=<TOKEN>` eller `Authorization: Bearer <TOKEN>`. TOKEN = env
    METRICS_TOKEN viss sett, elles den delte flåte-nøkkelen (_floate_token).
    Grafana → Prometheus skrapar dette, og du grafar pqtech_channel_value.
    """
    tok = os.environ.get("METRICS_TOKEN", "").strip() or _floate_token()
    gitt = request.args.get("token", "").strip()
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        gitt = auth[7:].strip()
    if not tok:
        return Response("# metrics ikkje konfigurert (manglar token)\n",
                        status=503, mimetype="text/plain")
    if gitt != tok:
        return Response("# ugyldig eller manglande token\n",
                        status=401, mimetype="text/plain")

    try:
        kanalar = _hent_kanalar_for_eksport()
    except Exception as e:
        return Response(f"# feil ved henting: {e}\n", status=500, mimetype="text/plain")

    def esc(s):
        return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    linjer = [
        "# HELP pqtech_channel_value Kanalverdi frå PQTech openDAQ",
        "# TYPE pqtech_channel_value gauge",
    ]
    node_tilstand = {}
    for k in kanalar:
        node = k.get("node_namn") or k.get("node_id") or ""
        node_id = k.get("node_id") or ""
        if node_id:
            cur = node_tilstand.get((node_id, node), 0)
            node_tilstand[(node_id, node)] = max(cur, 1 if k.get("tilkobla") else 0)
        verdi = k.get("verdi")
        if verdi is None:
            continue
        try:
            v = float(verdi)
        except (TypeError, ValueError):
            continue
        labels = (f'node="{esc(node)}",node_id="{esc(node_id)}",'
                  f'channel="{esc(k.get("namn") or "")}",'
                  f'unit="{esc(k.get("eining") or "")}",'
                  f'type="{esc(k.get("kanal_type") or "opendaq")}"')
        linjer.append(f"pqtech_channel_value{{{labels}}} {v}")

    if node_tilstand:
        linjer.append("# HELP pqtech_node_connected Node tilkobla (1=ja, 0=nei)")
        linjer.append("# TYPE pqtech_node_connected gauge")
        for (nid, nn), st in node_tilstand.items():
            linjer.append(f'pqtech_node_connected{{node="{esc(nn)}",node_id="{esc(nid)}"}} {st}')

    return Response("\n".join(linjer) + "\n",
                    mimetype="text/plain; version=0.0.4; charset=utf-8")


# ── API-nøklar + eksternt lese-API (/api/v1) ──────────────────────────────
# Hubben er offentleg eksponert via Cloudflare, så ein klient utanfor nettet
# (t.d. ein desktop-widget) treng berre ein nøkkel — ingen VPN. Nøklane vert
# administrerte her (session-auth), medan sjølve /api/v1-rutene autentiserer
# med nøkkelen (sjå brukar_auth.sjekk_auth).

@app.route("/api/api-nokler")
def api_nokler_liste():
    """List nøklar. Klarteksten finst ikkje — berre prefiks og metadata."""
    return jsonify({"nokler": [n.offentleg() for n in api_nokkel.les_nokler()]})


@app.route("/api/api-nokler", methods=["POST"])
def api_nokler_opprett():
    """Opprett nøkkel. Klarteksten vert returnert HER OG BERRE HER."""
    data = request.get_json(silent=True) or {}
    try:
        klartekst, nokkel = api_nokkel.opprett(
            data.get("namn", ""),
            utloep=data.get("utloep", ""),
            kanal_filter=data.get("kanal_filter") or [],
        )
    except OSError as e:
        return jsonify({"suksess": False, "feil": str(e)}), 500
    return jsonify({"suksess": True, "nokkel": klartekst, **nokkel.offentleg()})


@app.route("/api/api-nokler/<nokkel_id>", methods=["PUT"])
def api_nokler_endre(nokkel_id):
    """Slå ein nøkkel av eller på utan å slette han."""
    data = request.get_json(silent=True) or {}
    ok = api_nokkel.sett_aktivert(nokkel_id, bool(data.get("aktivert", True)))
    if not ok:
        return jsonify({"suksess": False, "feil": "Nøkkel ikkje funnen"}), 404
    return jsonify({"suksess": True})


@app.route("/api/api-nokler/<nokkel_id>", methods=["DELETE"])
def api_nokler_slett(nokkel_id):
    """Trekk tilbake ein nøkkel. Verkar med ein gong (ingen cache-forsinking)."""
    if not api_nokkel.slett(nokkel_id):
        return jsonify({"suksess": False, "feil": "Nøkkel ikkje funnen"}), 404
    return jsonify({"suksess": True})


def _v1_kanalar(nokkel) -> list:
    """Kanalane denne nøkkelen får sjå, i eit stabilt, klient-vennleg format.

    Bevisst smalare enn /api/hub/kanalar: eit eksternt API skal ikkje lekke
    intern struktur som endrar seg med kvar refaktorering.
    """
    kanalar = _hent_kanalar_for_eksport()
    if nokkel is not None:
        kanalar = api_nokkel.filtrer_kanalar(nokkel, kanalar)
    ut = []
    for k in kanalar:
        ut.append({
            "node": k.get("node_namn") or k.get("node_id") or "",
            "namn": k.get("namn") or "",
            "verdi": k.get("verdi"),
            "eining": k.get("eining") or "",
            "type": k.get("kanal_type") or "opendaq",
            "tilkobla": bool(k.get("tilkobla")),
        })
    return ut


@app.route("/api/v1/info")
def api_v1_info():
    """Kva denne hubben er, og kva nøkkelen din har tilgang til."""
    nokkel = getattr(g, "api_nokkel", None)
    return jsonify({
        "namn": socket.gethostname(),
        "modus": "hub" if HUB_MODUS else ("direkte" if SIRIUS_DIREKTE else "opendaq"),
        "versjon": oppdatering.les_versjon().get("sha", ""),
        "nokkel_namn": nokkel.namn if nokkel else None,
        "kanal_filter": nokkel.kanal_filter if nokkel else [],
        "antal_kanalar": len(_v1_kanalar(nokkel)),
    })


@app.route("/api/v1/kanalar")
def api_v1_kanalar():
    """Siste verdi for kvar kanal. For klientar som pollar."""
    from datetime import datetime
    nokkel = getattr(g, "api_nokkel", None)
    return jsonify({
        "tid": datetime.now().isoformat(timespec="seconds"),
        "kanalar": _v1_kanalar(nokkel),
    })


@app.route("/api/v1/straum")
def api_v1_straum():
    """Server-Sent Events: same nyttelast som /api/v1/kanalar, med jamne mellomrom.

    Éi lang HTTPS-tilkobling i staden for polling. `?intervall=<sek>` styrer
    takten (0.5-60 s). Klienten treng berre lytte; EventSource i nettlesar og
    `requests`-strøyming i Python handterer begge dette direkte.
    """
    import json
    import time
    from datetime import datetime

    nokkel = getattr(g, "api_nokkel", None)
    try:
        intervall = float(request.args.get("intervall", 2.0))
    except ValueError:
        intervall = 2.0
    intervall = max(0.5, min(60.0, intervall))

    def generer():
        # Kommentar-linje med ein gong: får proxyar til å sende headerane
        # vidare, så klienten veit at tilkoblinga står.
        yield ": pqtech straum open\n\n"
        while True:
            nyttelast = {
                "tid": datetime.now().isoformat(timespec="seconds"),
                "kanalar": _v1_kanalar(nokkel),
            }
            yield f"data: {json.dumps(nyttelast)}\n\n"
            time.sleep(intervall)

    return Response(
        stream_with_context(generer()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # hindrar at proxyar bufrar straumen
            "Connection": "keep-alive",
        },
    )


@app.route("/api/influx/konfig")
def api_influx_konfig_hent():
    """InfluxDB-deling konfig (token maska til token_satt)."""
    return jsonify(influx_pusher.konfig_offentleg())


@app.route("/api/influx/konfig", methods=["PUT"])
def api_influx_konfig_sett():
    """Lagre InfluxDB-konfig. token utelate => behald, tom => fjern."""
    data = request.get_json(silent=True) or {}
    influx_pusher.lagre_konfig(data)
    return jsonify({"suksess": True, **influx_pusher.konfig_offentleg()})


@app.route("/api/influx/test", methods=["POST"])
def api_influx_test():
    """Skriv kanalverdiane til Influx no (for å teste oppsettet)."""
    ok, melding = influx_pusher.skriv_ein_gong(_hent_kanalar_for_eksport)
    return jsonify({"suksess": ok, "melding": melding})


def _emc_hent_vindu():
    """(samples (N,8), sample_rate, klar, {idx:(namn,eining)}) for EMC-FFT,
    eller None. Berre i SIRIUS-direkte-modus (rå ADC-bølgjeform)."""
    if not SIRIUS_DIREKTE:
        return None
    konf = emc_pusher.les_konfig()
    f0 = float(konf.get("nettfrekvens", 50)) or 50.0
    syk = int(konf.get("syklusar", 10))
    n = int(syk * 20000 / f0) + 256
    res = _sirius_hent_raw_vindu(n)
    if not res:
        return None
    samples, sr, klar = res
    kanalar = {}
    try:
        for k in les_konfig():
            if getattr(k, "aktiv", True):
                kanalar[k.indeks] = (k.namn, k.enhet)
    except Exception:
        pass
    return samples, sr, klar, kanalar


@app.route("/api/emc/konfig")
def api_emc_konfig_hent():
    """EMC/FFT-analyse konfig."""
    return jsonify(emc_pusher.konfig_offentleg())


@app.route("/api/emc/konfig", methods=["PUT"])
def api_emc_konfig_sett():
    """Lagre EMC-konfig (nettfrekvens, harmoniske, syklusar, fft-bins, intervall)."""
    data = request.get_json(silent=True) or {}
    emc_pusher.lagre_konfig(data)
    return jsonify({"suksess": True, **emc_pusher.konfig_offentleg()})


@app.route("/api/emc/test", methods=["POST"])
def api_emc_test():
    """Rekn + skriv EMC-data no (for å teste oppsettet).

    Hub-modus: analyser dei bridga kanalane (hub_emc). Node-modus: analyser
    den lokale SIRIUS-bølgjeforma (emc_pusher)."""
    if HUB_MODUS:
        try:
            import hub_emc
            ok, melding = hub_emc.samle_og_skriv()
        except Exception as e:  # noqa: BLE001
            ok, melding = False, str(e)
    else:
        ok, melding = emc_pusher.skriv_ein_gong(_emc_hent_vindu)
    return jsonify({"suksess": ok, "melding": melding})


@app.route("/api/emc-ingest", methods=["POST"])
def api_emc_ingest():
    """Mottak: nodar streamar ferdig-rekna EMC-linjer hit (line-protocol).

    Noden reknar EMC lokalt frå rå bølgjeform og sender berre dei små
    resultata over brua; hubben skriv dei til SIN InfluxDB (Share to Grafana).
    Validerer Bearer-token som /api/ingest.
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    forventa = ""
    try:
        forventa = les_push_konfig().ingest_token
    except Exception:
        pass
    if not forventa:
        forventa = os.environ.get("INGEST_TOKEN", "")
    if not forventa:
        return jsonify({"suksess": False, "melding": "Ingest ikkje konfigurert"}), 503
    if token != forventa:
        return jsonify({"suksess": False, "melding": "Ugyldig token"}), 401

    data = request.get_json(silent=True) or {}
    linjer = data.get("linjer", [])
    if not isinstance(linjer, list) or not linjer:
        return jsonify({"suksess": True, "melding": "ingen linjer"})
    # Berre pqtech_* measurements (unngå at nokon skriv vilkårleg)
    linjer = [str(l) for l in linjer if str(l).startswith("pqtech_")]
    ok, melding = emc_pusher.skriv_linjer(linjer)
    # Arkiver rå CSV til NAS (no-op når deaktivert; ikkje-blokkerande kø)
    try:
        import raa_fil_skrivar
        raa_fil_skrivar.skriv_punkt(raa_fil_skrivar.parse_line_protocol(linjer))
    except Exception:
        pass
    return jsonify({"suksess": ok, "melding": melding, "mottatt": len(linjer)})


# --- Hub-lager: persistent lagring av kanaldata på hubben ---

@app.route("/api/hub-lager/konfig")
def api_hub_lager_konfig_hent():
    """Hub-lager konfig + køyrestatus."""
    return jsonify(hub_lager.konfig_offentleg())


@app.route("/api/hub-lager/konfig", methods=["PUT"])
def api_hub_lager_konfig_sett():
    """Lagre hub-lager-konfig (aktivert, db_sti, retensjon, intervall, maks_mb)."""
    data = request.get_json(silent=True) or {}
    hub_lager.lagre_konfig(data)
    return jsonify({"suksess": True, **hub_lager.konfig_offentleg()})


@app.route("/api/hub-lager/status")
def api_hub_lager_status():
    """Køyrestatus for hub-lageret (rader, storleik, per-node)."""
    return jsonify(hub_lager.status())


# --- Modbus-lager: store-and-forward av PQube/Modbus-data til hub ---

@app.route("/api/modbus-lager/konfig")
def api_modbus_lager_konfig_hent():
    """Modbus-lager konfig + køyrestatus (usendte, storleik, backfill)."""
    try:
        import modbus_lager
        return jsonify(modbus_lager.konfig_offentleg())
    except Exception as e:
        return jsonify({"aktivert": False, "feil": str(e)})


@app.route("/api/modbus-lager/konfig", methods=["PUT"])
def api_modbus_lager_konfig_sett():
    """Lagre modbus-lager-konfig (aktivert, intervall, retensjon, batch)."""
    data = request.get_json(silent=True) or {}
    try:
        import modbus_lager
        modbus_lager.lagre_konfig(data)
        if modbus_lager.les_konfig().get("aktivert"):
            modbus_lager.start()  # idempotent — start trådar viss nyleg aktivert
        return jsonify({"suksess": True, **modbus_lager.konfig_offentleg()})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/modbus-lager/status")
def api_modbus_lager_status():
    """Køyrestatus for modbus-lageret."""
    try:
        import modbus_lager
        return jsonify(modbus_lager.status())
    except Exception as e:
        return jsonify({"feil": str(e)})


# --- Rå-fil-arkiv (CSV til NAS/CIFS) ---

@app.route("/api/raa-fil/konfig")
def api_raa_fil_konfig_hent():
    """Rå-fil-arkiv konfig + status (katalog skrivbar, kø, tal skrive)."""
    try:
        import raa_fil_skrivar
        return jsonify(raa_fil_skrivar.konfig_offentleg())
    except Exception as e:
        return jsonify({"aktivert": False, "feil": str(e)})


@app.route("/api/nas/oppdag", methods=["POST"])
def api_nas_oppdag():
    """Skann LAN for SMB-delingar. Valfrie creds for å liste verna delingar."""
    data = request.get_json(silent=True) or {}
    try:
        import nas_manager
        return jsonify({"suksess": True, **nas_manager.oppdag(
            brukar=data.get("brukar", ""), passord=data.get("passord", ""))})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/nas/monter", methods=["POST"])
def api_nas_monter():
    """Monter ei valt CIFS-deling (//server/share) i containeren."""
    data = request.get_json(silent=True) or {}
    try:
        import nas_manager
        ok, melding = nas_manager.monter(
            server=data.get("server", ""), share=data.get("share", ""),
            brukar=data.get("brukar", ""), passord=data.get("passord", ""),
            mountpunkt=data.get("mountpunkt", nas_manager.STANDARD_MNT),
            domene=data.get("domene", ""))
        return jsonify({"suksess": ok, "melding": melding, **nas_manager.konfig_offentleg()})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/nas/avmonter", methods=["POST"])
def api_nas_avmonter():
    try:
        import nas_manager
        ok, melding = nas_manager.avmonter()
        return jsonify({"suksess": ok, "melding": melding, **nas_manager.konfig_offentleg()})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/nas/status")
def api_nas_status():
    try:
        import nas_manager
        return jsonify(nas_manager.konfig_offentleg())
    except Exception as e:
        return jsonify({"montert": False, "feil": str(e)})


@app.route("/api/wifi/status")
def api_wifi_status():
    """Noverande WiFi-tilstand på verten (SSID, IP, signal, radio)."""
    try:
        import wifi_manager
        return jsonify(wifi_manager.status())
    except Exception as e:
        return jsonify({"nmcli_tilgjengeleg": False, "feil": str(e)})


@app.route("/api/wifi/skann", methods=["POST"])
def api_wifi_skann():
    """Skann etter tilgjengelege WiFi-nett."""
    try:
        import wifi_manager
        return jsonify(wifi_manager.skann())
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/wifi/koble", methods=["POST"])
def api_wifi_koble():
    """Kople verten til eit WiFi-nett (SSID + passord)."""
    data = request.get_json(silent=True) or {}
    try:
        import wifi_manager
        ok, melding = wifi_manager.koble_til(
            ssid=data.get("ssid", ""), passord=data.get("passord", ""),
            skjult=bool(data.get("skjult", False)))
        return jsonify({"suksess": ok, "melding": melding, **wifi_manager.status()})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/wifi/gloym", methods=["POST"])
def api_wifi_gloym():
    """Slett den lagra profilen for eit WiFi-nett."""
    data = request.get_json(silent=True) or {}
    try:
        import wifi_manager
        ok, melding = wifi_manager.gløym(ssid=data.get("ssid", ""))
        return jsonify({"suksess": ok, "melding": melding, **wifi_manager.status()})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/raa-fil/konfig", methods=["PUT"])
def api_raa_fil_konfig_sett():
    """Lagre rå-fil-konfig (aktivert, katalog). Katalog kan vere ein
    CIFS/NAS-mount inne i containeren (t.d. /data/nas/maalingar)."""
    data = request.get_json(silent=True) or {}
    try:
        import raa_fil_skrivar
        raa_fil_skrivar.lagre_konfig(data)
        raa_fil_skrivar.start()
        return jsonify({"suksess": True, **raa_fil_skrivar.konfig_offentleg()})
    except Exception as e:
        return jsonify({"suksess": False, "melding": str(e)}), 500


@app.route("/api/hub-lager/data")
def api_hub_lager_data():
    """Hent lagra punkt. Query: node_id, kanal, fra_ms, til_ms, limit."""
    rader = hub_lager.hent_data(
        node_id=request.args.get("node_id", ""),
        kanal=request.args.get("kanal", ""),
        frå_ms=request.args.get("fra_ms", 0, type=int),
        til_ms=request.args.get("til_ms", 0, type=int),
        limit=request.args.get("limit", 1000, type=int),
    )
    return jsonify({"rader": rader})


@app.route("/api/hub-lager/eksport.csv")
def api_hub_lager_eksport():
    """Streame lagra kanaldata som CSV-nedlasting."""
    gen = hub_lager.eksport_csv(
        node_id=request.args.get("node_id", ""),
        kanal=request.args.get("kanal", ""),
        frå_ms=request.args.get("fra_ms", 0, type=int),
        til_ms=request.args.get("til_ms", 0, type=int),
        limit=request.args.get("limit", 100000, type=int),
    )
    return Response(gen, mimetype="text/csv", headers={
        "Content-Disposition": "attachment; filename=hub_kanaldata.csv"})


@app.route("/api/hub/kanal-ranges")
def api_hub_kanal_ranges_hent():
    """Hent kanal-range overstyringer."""
    if not HUB_MODUS:
        return jsonify({"overstyringer": []})
    try:
        return jsonify({"overstyringer": hent_kanal_ranges_dict()})
    except Exception as e:
        return jsonify({"overstyringer": [], "feil": str(e)})


@app.route("/api/hub/kanal-ranges", methods=["PUT"])
def api_hub_kanal_ranges_oppdater():
    """Lagre kanal-range overstyringer."""
    if not HUB_MODUS:
        return jsonify({"suksess": False, "melding": "Hub ikkje aktiv"}), 400
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"suksess": False, "melding": "Ugyldig JSON"}), 400
    overstyringer = data.get("overstyringer", [])
    if not isinstance(overstyringer, list):
        return jsonify({"suksess": False, "melding": "'overstyringer' må vere ei liste"}), 400
    ok, melding = oppdater_kanal_ranges(overstyringer)
    return jsonify({"suksess": ok, "melding": melding}), 200 if ok else 400


@app.route("/api/hub/logg")
def api_hub_logg():
    """Hub-loggar (ringbuffer)."""
    if not HUB_MODUS:
        return jsonify({"linjer": [], "antall": 0})
    antall = request.args.get("antall", 200, type=int)
    linjer = _hub_hent_logg(min(antall, 500))
    return jsonify({"linjer": linjer, "antall": len(linjer)})


# --- Modbus API ---

@app.route("/api/modbus/test", methods=["POST"])
def api_modbus_test():
    """Test modbus-tilkobling og les register.

    Body: { host, port, unit_id, timeout_ms, registers: [ModbusRegister dict] }
    Return: { suksess, melding, verdiar: [{namn, adresse, raa, verdi, feil}] }

    Tilgjengeleg uavhengig av hub-modus så brukar kan verifisere register
    før dei lagrar konfig.
    """
    data = request.get_json(silent=True) or {}
    host = str(data.get("host", "")).strip()
    if not host:
        return jsonify({"suksess": False, "melding": "Mangler 'host'", "verdiar": []}), 400

    try:
        port = int(data.get("port", 502))
        unit_id = int(data.get("unit_id", 1))
        timeout_ms = int(data.get("timeout_ms", 2000))
        base_adresse = int(data.get("base_adresse", 0))
    except (TypeError, ValueError):
        return jsonify({"suksess": False, "melding": "Ugyldig port/unit_id/timeout/base_adresse", "verdiar": []}), 400

    from hub_konfig import ModbusRegister
    import modbus_klient as mk

    regs_data = data.get("registers", [])
    if not isinstance(regs_data, list):
        return jsonify({"suksess": False, "melding": "'registers' må vere ei liste", "verdiar": []}), 400

    try:
        registers = [ModbusRegister.fraa_dict(r) for r in regs_data]
    except Exception as e:
        return jsonify({"suksess": False, "melding": f"Ugyldig register: {e}", "verdiar": []}), 400

    resultat = mk.test_tilkobling(host, port, unit_id, timeout_ms, registers,
                                   base_adresse=base_adresse)
    status_code = 200 if resultat.get("suksess") else 502
    return jsonify(resultat), status_code


# --- System / Oppdatering API ---

@app.route("/api/system/versjon")
def api_system_versjon():
    """Noverande versjon (frå versjon.json)."""
    return jsonify(oppdatering.les_versjon())


@app.route("/api/system/sjekk-oppdatering")
def api_system_sjekk_oppdatering():
    """Sjekk GitHub for ny versjon."""
    try:
        noverande = oppdatering.les_versjon()
        github = oppdatering.sjekk_github()
        return jsonify({
            "noverande": noverande,
            "github": github,
            "oppdatering_tilgjengeleg": noverande.get("sha") != github["sha"],
        })
    except Exception as e:
        return jsonify({"feil": str(e)}), 500


@app.route("/api/system/support-bundle")
def api_system_support_bundle():
    """Lag ein ZIP med (redigert) konfig, systeminfo og logg for support.

    Hemmelege felt (token/passord/secret) vert redigerte bort før zipping,
    slik at pakken trygt kan sendast til PQ Tech support."""
    if "brukar" not in session:
        return jsonify({"feil": "Ikkje innlogga"}), 401

    import io
    import zipfile
    import json as _json
    import platform
    import time as _time

    HEMMELEG = ("token", "passord", "password", "secret")

    def reduser(obj):
        if isinstance(obj, dict):
            ut = {}
            for k, v in obj.items():
                if any(h in k.lower() for h in HEMMELEG) and v:
                    ut[k] = "***REDIGERT***"
                else:
                    ut[k] = reduser(v)
            return ut
        if isinstance(obj, list):
            return [reduser(x) for x in obj]
        return obj

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # 1) Systeminfo
        info = [
            f"Generert: {_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Hostname: {socket.gethostname()}",
            f"Modus: {'hub' if HUB_MODUS else 'node/direkte'}",
            f"Platform: {platform.platform()}",
            f"Python: {platform.python_version()}",
        ]
        try:
            info.append(f"Versjon: {_json.dumps(oppdatering.les_versjon())}")
        except Exception as e:
            info.append(f"Versjon: (ukjend: {e})")
        for k in sorted(os.environ):
            if any(h in k.lower() for h in HEMMELEG):
                continue
            if k.startswith(("OPENDAQ", "OPPDATER", "INGEST", "TZ", "HOSTNAME", "PARENT")):
                info.append(f"env {k}={os.environ[k]}")
        z.writestr("system_info.txt", "\n".join(info))

        # 2) Konfig (redigert)
        for sti in sorted(glob_mod.glob("/data/konfig/*.json")):
            namn = os.path.basename(sti)
            try:
                with open(sti, encoding="utf-8") as f:
                    data = _json.load(f)
                z.writestr(f"konfig/{namn}",
                           _json.dumps(reduser(data), indent=2, ensure_ascii=False))
            except Exception as e:
                z.writestr(f"konfig/{namn}.FEIL.txt", str(e))

        # 3) Logg (intern ring-buffer om tilgjengeleg)
        try:
            if SIRIUS_DIREKTE:
                z.writestr("logg/sirius.log", "\n".join(_sirius_hent_logg(500)))
            else:
                z.writestr("logg/README.txt",
                           "Ingen lokal SIRIUS-logg (hub-modus).\n"
                           "Full logg: 'sudo docker logs pqtech-opendaq' på verten.")
        except Exception as e:
            z.writestr("logg/FEIL.txt", str(e))

        # 4) Ingest-statistikk (hub)
        try:
            with _ingest_lock:
                stats = dict(_ingest_stats)
            z.writestr("ingest_stats.json", _json.dumps(stats, indent=2, default=str))
        except Exception:
            pass

    buf.seek(0)
    fnamn = f"support-{socket.gethostname()}-{_time.strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename={fnamn}"})


@app.route("/api/system/oppdater-konfig")
def api_system_oppdater_konfig_hent():
    """Repo-URL/branch for oppdatering. Token vert aldri returnert
    (berre token_satt: bool)."""
    return jsonify(oppdatering.hent_oppdater_konfig_offentleg())


@app.route("/api/system/oppdater-konfig", methods=["PUT"])
def api_system_oppdater_konfig_sett():
    """Lagre repo-URL/branch/token. token utelate => behald; tom => fjern."""
    data = request.get_json(silent=True) or {}
    repo_url = data.get("repo_url", "")
    branch = data.get("branch", "main")
    token = data.get("token", None)
    return jsonify(oppdatering.lagre_oppdater_konfig(repo_url, branch, token))


def _floate_token() -> str:
    """Delt flaate-token: parent_token (node) eller ingest_token (hub) eller env."""
    try:
        pk = les_push_konfig()
        if pk.parent_token:
            return pk.parent_token
        if pk.ingest_token:
            return pk.ingest_token
    except Exception:
        pass
    return os.environ.get("INGEST_TOKEN", "")


def _floate_auth_ok() -> bool:
    """True viss Authorization: Bearer <token> matchar flaate-token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    forventa = _floate_token()
    return bool(forventa) and auth[7:].strip() == forventa


@app.route("/api/system/oppdater", methods=["POST"])
def api_system_oppdater():
    """Last ned og installer oppdatering, deretter restart.

    Autorisering: innlogga admin (session) ELLER gyldig flaate-token (hub->node).
    Hub kan sende {repo_url, branch, token} i body for å propagere
    oppdaterings-kjelda til heile flåten før nedlasting.
    """
    if "brukar" not in session and not _floate_auth_ok():
        return jsonify({"suksess": False, "feil": "Ikkje autorisert"}), 401
    data = request.get_json(silent=True) or {}
    if data.get("repo_url"):
        oppdatering.lagre_oppdater_konfig(
            data.get("repo_url", ""), data.get("branch", "main"),
            data.get("token", None))
    try:
        resultat = oppdatering.last_ned_og_oppdater()
        # Planlegg restart etter at responsen er sendt
        threading.Timer(2.0, lambda: os._exit(0)).start()
        return jsonify(resultat)
    except Exception as e:
        return jsonify({"suksess": False, "feil": str(e)}), 500


@app.route("/api/system/oppdater-floate", methods=["POST"])
def api_system_oppdater_floate():
    """Oppdater heile flåten: trigg alle openDAQ-nodar (dei hentar sjølv frå
    same repo), deretter oppdater hubben sjølv sist. Admin-only (session)."""
    resultat = {"nodar": [], "hub": None}
    konf = oppdatering.hent_oppdater_konfig()
    body = {"repo_url": konf["repo_url"], "branch": konf["branch"]}
    if konf.get("token"):
        body["token"] = konf["token"]
    tok = _floate_token()

    try:
        nodar = [n for n in les_hub_konfig().nodar
                 if (not getattr(n, "type", None) or n.type == "opendaq")]
    except Exception as e:
        nodar = []
        resultat["feil"] = f"Kunne ikkje lese nodar: {e}"

    for n in nodar:
        host = str(n.adresse).split(":")[0].strip()
        url = f"http://{host}:{_NODE_PROXY_PORT}/api/system/oppdater"
        try:
            r = _http_proxy.post(
                url, json=body,
                headers={"Authorization": f"Bearer {tok}"} if tok else {},
                timeout=60)
            j = {}
            try:
                j = r.json()
            except Exception:
                pass
            ok = (r.status_code == 200) and bool(j.get("suksess", r.status_code == 200))
            resultat["nodar"].append({
                "id": n.id, "namn": n.namn, "suksess": ok,
                "melding": j.get("melding") or j.get("versjon"),
                "feil": j.get("feil") if not ok else None,
            })
        except Exception as e:
            resultat["nodar"].append({
                "id": n.id, "namn": n.namn, "suksess": False, "feil": str(e)})

    # Hubben sjølv til slutt (restart kuttar samband — difor sist)
    try:
        resultat["hub"] = oppdatering.last_ned_og_oppdater()
        threading.Timer(3.0, lambda: os._exit(0)).start()
    except Exception as e:
        resultat["hub"] = {"suksess": False, "feil": str(e)}

    return jsonify(resultat)


@app.route("/api/system/restart", methods=["POST"])
def api_system_restart():
    """Restart containeren (os._exit → Docker restart-policy startar på nytt)."""
    log.info("Restart forespurt via API — avsluttar om 2 sek...")
    threading.Timer(2.0, lambda: os._exit(0)).start()
    return jsonify({"suksess": True, "melding": "Omstart om 2 sekund..."})


# --- Buffer API ---

# --- Push/ingest API ---

@app.route("/api/push/konfig")
def api_push_konfig_hent():
    """Hent push-konfig (kva parent denne konteinaren pushar til)."""
    from dataclasses import asdict
    konfig = les_push_konfig()
    return jsonify(asdict(konfig))


@app.route("/api/push/konfig", methods=["PUT"])
def api_push_konfig_oppdater():
    """Oppdater push-konfig + restart push-pusher."""
    data = request.get_json(silent=True) or {}
    konfig, feil = valider_push_konfig(data)
    if feil:
        return jsonify({"suksess": False, "melding": feil}), 400
    if not lagre_push_konfig(konfig):
        return jsonify({"suksess": False, "melding": "Lagring feila"}), 500
    # Be sirius_server om å restarte pushar med ny konfig
    if SIRIUS_DIREKTE:
        try:
            from sirius_server import oppdater_push_konfig as _oppdater_push
            _oppdater_push(konfig)
        except ImportError:
            pass
        except Exception as e:
            return jsonify({"suksess": False,
                            "melding": f"Konfig lagra, men restart feila: {e}"}), 500
    return jsonify({"suksess": True, "melding": "Push-konfig lagra"})


@app.route("/api/push/status")
def api_push_status():
    """Status for utgåande push-pusher (kva denne konteinaren sender)."""
    if SIRIUS_DIREKTE:
        try:
            from sirius_server import hent_push_status as _push_status
            return jsonify(_push_status())
        except ImportError:
            pass
        except Exception as e:
            return jsonify({"konfigurert": False, "feil": str(e)})
    return jsonify({"konfigurert": False, "kjorer": False})


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    """Mottak-endepunkt: barn-nodar POST-ar JSON-batchar hit.

    Validerer Authorization: Bearer <token> mot push_konfig.ingest_token
    (eller env INGEST_TOKEN som fallback).

    Body: {node_id, node_namn, ts, kanalar: {namn: verdi}, buffer_lag_ms}

    Dag 1: lagrar batch i RAM-ringbuffer + loggar. Dag 2: injiserer i
    openDAQ-pipeline.
    """
    # Token-validering
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()

    forventa = ""
    try:
        forventa = les_push_konfig().ingest_token
    except Exception:
        pass
    if not forventa:
        forventa = os.environ.get("INGEST_TOKEN", "")

    if not forventa:
        return jsonify({"suksess": False,
                        "melding": "Ingest ikkje konfigurert (manglar token)"}), 503
    if token != forventa:
        with _ingest_lock:
            _ingest_stats["avvist"] += 1
        return jsonify({"suksess": False, "melding": "Ugyldig token"}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"suksess": False, "melding": "Ugyldig JSON"}), 400

    node_id = str(data.get("node_id", "")).strip()
    if not node_id:
        return jsonify({"suksess": False, "melding": "Manglar node_id"}), 400

    kanalar = data.get("kanalar", {})
    if not isinstance(kanalar, dict):
        return jsonify({"suksess": False, "melding": "kanalar maa vere eit objekt"}), 400

    ts = float(data.get("ts", 0.0))
    node_namn = str(data.get("node_namn", node_id))
    buffer_lag_ms = float(data.get("buffer_lag_ms", 0))

    batch = {
        "ts": ts,
        "node_namn": node_namn,
        "kanalar": kanalar,
        "buffer_lag_ms": buffer_lag_ms,
        "mottatt_ts": __import__("time").time(),
    }

    with _ingest_lock:
        _ingest_data[node_id].append(batch)
        _ingest_stats["totalt"] += 1
        _ingest_stats["siste_ts"] = batch["mottatt_ts"]

    # Persistent lagring på hubben (ikkje-blokkerande kø; no-op når deaktivert).
    try:
        hub_lager.lagre(node_id, node_namn, ts, kanalar)
    except Exception:
        pass

    # Injiser verdiar i hub si openDAQ-pipeline (DC-relay for skalarar,
    # DataPacket.send_packet for sample-arrays). node_namn er primær
    # matche-nøkkel sidan push-konfig.node_id ofte avvikar frå hub-id.
    injisert = 0
    if HUB_MODUS and kanalar:
        # Sjekk om vi har sample-arrays (raw mode) eller skalarar
        has_arrays = any(isinstance(v, list) for v in kanalar.values())
        sample_rate = int(data.get("sample_rate", 20000))
        try:
            from hub_server import injiser_push_verdiar
            if has_arrays:
                from hub_server import injiser_push_array
                # Skalar-deler først (DC-relay)
                skalar = {k: v for k, v in kanalar.items()
                          if not isinstance(v, list)}
                if skalar:
                    injisert = injiser_push_verdiar(node_namn, skalar)
                    if injisert == 0:
                        injisert = injiser_push_verdiar(node_id, skalar)
                # Array-deler (raw waveform)
                arrays = {k: v for k, v in kanalar.items()
                          if isinstance(v, list)}
                arr_inj = injiser_push_array(node_namn, arrays, sample_rate)
                if arr_inj == 0:
                    arr_inj = injiser_push_array(node_id, arrays, sample_rate)
                injisert += arr_inj
            else:
                injisert = injiser_push_verdiar(node_namn, kanalar)
                if injisert == 0:
                    injisert = injiser_push_verdiar(node_id, kanalar)
        except ImportError:
            pass
        except Exception:
            pass

    if _ingest_stats["totalt"] % 100 == 1:
        latens_ms = max(0.0, (batch["mottatt_ts"] - ts) * 1000.0) if ts > 0 else 0.0
        try:
            __import__("logging").getLogger("ingest").info(
                f"Ingest #{_ingest_stats['totalt']}: node={node_id} "
                f"({node_namn}), {len(kanalar)} kanalar, "
                f"injisert={injisert}, latens={latens_ms:.0f}ms")
        except Exception:
            pass

    return jsonify({"suksess": True, "mottatt": len(kanalar),
                    "injisert": injisert})


@app.route("/api/ingest/status")
def api_ingest_status():
    """Status for ingest-mottak: kva nodar pushar til oss."""
    with _ingest_lock:
        nodar = []
        for node_id, batch_q in _ingest_data.items():
            if not batch_q:
                continue
            siste = batch_q[-1]
            nodar.append({
                "node_id": node_id,
                "node_namn": siste.get("node_namn", node_id),
                "siste_ts": siste.get("ts", 0),
                "siste_mottatt": siste.get("mottatt_ts", 0),
                "antal_kanalar": len(siste.get("kanalar", {})),
                "antal_batchar": len(batch_q),
                "siste_lag_ms": siste.get("buffer_lag_ms", 0),
            })
        stats = dict(_ingest_stats)
    return jsonify({"nodar": nodar, "stats": stats})


@app.route("/api/ingest/data/<node_id>")
def api_ingest_data(node_id: str):
    """Hent siste batchar frå ein spesifikk node (for live-visning)."""
    limit = request.args.get("limit", 50, type=int)
    limit = max(1, min(limit, 200))
    with _ingest_lock:
        batchar = list(_ingest_data.get(node_id, []))
    return jsonify({"node_id": node_id,
                    "batchar": batchar[-limit:]})


# --- Buffer API ---

@app.route("/api/buffer/status")
def api_buffer_status():
    if SIRIUS_DIREKTE:
        return jsonify(_buffer_hent_status())
    return jsonify({"aktivert": False, "totalt_rader": 0})


@app.route("/api/buffer/tom", methods=["POST"])
def api_buffer_tom():
    """Tøm målebufferen på noden (måledata, hendingar, MQTT-logg)."""
    if not SIRIUS_DIREKTE:
        return jsonify({"suksess": False,
                        "melding": "Buffer berre tilgjengeleg i SIRIUS-direkte-modus"}), 400
    res = _buffer_tom()
    return jsonify(res)


@app.route("/api/buffer/data")
def api_buffer_data():
    if not SIRIUS_DIREKTE:
        return jsonify({"rader": []})
    etter_id = request.args.get("etter_id", 0, type=int)
    limit = request.args.get("limit", 10000, type=int)
    limit = min(limit, 50000)  # Maksgrense
    rader = _buffer_hent_data(etter_id=etter_id, limit=limit)
    return jsonify({"rader": rader})


@app.route("/api/buffer/ack", methods=["POST"])
def api_buffer_ack():
    data = request.get_json(silent=True) or {}
    opp_til_id = data.get("opp_til_id", 0)
    if not opp_til_id:
        return jsonify({"suksess": False, "melding": "Mangler opp_til_id"}), 400
    if SIRIUS_DIREKTE:
        ok = _buffer_marker_synk(opp_til_id)
        if ok:
            return jsonify({"suksess": True, "melding": "Synkronisert"})
        return jsonify({"suksess": False, "melding": "Markering feila"}), 500
    return jsonify({"suksess": False, "melding": "Buffer ikkje tilgjengeleg"}), 503


@app.route("/api/buffer/konfig")
def api_buffer_konfig_hent():
    if SIRIUS_DIREKTE:
        return jsonify(_buffer_hent_konfig())
    from dataclasses import asdict
    return jsonify(asdict(les_buffer_konfig()))


@app.route("/api/buffer/konfig", methods=["PUT"])
def api_buffer_konfig_oppdater():
    data = request.get_json(silent=True) or {}
    konfig, feil = valider_buffer_konfig(data)
    if feil:
        return jsonify({"suksess": False, "melding": feil}), 400
    if SIRIUS_DIREKTE:
        ok = _buffer_oppdater(konfig)
    else:
        from buffer_konfig import lagre_buffer_konfig
        ok = lagre_buffer_konfig(konfig)
    if ok:
        return jsonify({"suksess": True, "melding": "Buffer-konfig lagra"})
    return jsonify({"suksess": False, "melding": "Lagring feila"}), 500


@app.route("/api/buffer/hendingar")
def api_buffer_hendingar():
    if not SIRIUS_DIREKTE:
        return jsonify({"hendingar": []})
    etter_id = request.args.get("etter_id", 0, type=int)
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 500)
    hendingar = _buffer_hent_hendingar(limit=limit, etter_id=etter_id)
    return jsonify({"hendingar": hendingar})


@app.route("/api/buffer/mqtt-logg")
def api_buffer_mqtt_logg():
    if not SIRIUS_DIREKTE:
        return jsonify({"rader": []})
    etter_tid = request.args.get("etter_tid", 0, type=int)
    limit = request.args.get("limit", 100, type=int)
    limit = min(limit, 5000)
    rader = _buffer_hent_mqtt_logg(limit=limit, etter_tid=etter_tid)
    return jsonify({"rader": rader})


@app.route("/api/buffer/lagring")
def api_buffer_lagring():
    if not SIRIUS_DIREKTE:
        return jsonify({"sti": "", "ssd_aktiv": False, "ledig_mb": 0, "brukt_mb": 0})
    return jsonify(_buffer_hent_lagring())


@app.route("/api/hub/buffer/status")
def api_hub_buffer_status():
    if not HUB_MODUS:
        return jsonify({"aktivert": False})
    return jsonify(hent_hub_buffer_status())


# --- React Frontend ---

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")


def _send_index():
    """Server index.html med no-cache, og injiser <base href> når vi er bak
    node-proxyen.

    Asset-filene er innhalds-hasha (kan cachast evig), men index.html MÅ
    hentast fersk — elles peikar ein cacha index på gamle/borte asset-hashar
    etter ei oppdatering (blank side / 404).

    SPA-en brukar relative asset-stiar (./assets/...). Bak node-proxyen sender
    hubben X-Forwarded-Prefix=/node-proxy/<id>. Vi injiserer <base href=
    "<prefix>/"> slik at relative stiar ALLTID løyser mot proxy-prefikset —
    sjølv om dokument-URL-en er ukanonisk (t.d. dobbel-prefiks). Det fjernar
    avhengigheita av 307-redirect-omskriving og hindrar dobbel-prefiks-404."""
    prefix = request.headers.get("X-Forwarded-Prefix", "").rstrip("/")
    sti = os.path.join(FRONTEND_DIR, "index.html")
    if prefix:
        with open(sti, encoding="utf-8") as f:
            html = f.read()
        if "<base " not in html:
            html = html.replace("<head>", f'<head>\n    <base href="{prefix}/">', 1)
        resp = Response(html, mimetype="text/html")
    else:
        resp = send_file(sti)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/")
def index():
    return _send_index()


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_file(os.path.join(FRONTEND_DIR, "assets", filename))


@app.route("/<path:path>")
def catch_all(path):
    if path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404
    file_path = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(file_path):
        return send_file(file_path)
    return _send_index()


# --- Legacy HTML removed (migrated to React in frontend/) ---


# Start Influx-skrivar (deler kanalverdiar til Grafana via InfluxDB v2).
# Køyrer berre når den er konfigurert/aktivert (sjå influx_pusher.les_konfig).
try:
    influx_pusher.start(_hent_kanalar_for_eksport)
except Exception as _e:  # noqa: BLE001
    print(f"Influx-skrivar starta ikkje: {_e}")

# Start EMC/FFT-skrivar (harmoniske, THD, spektrum → InfluxDB). Berre nyttig
# i SIRIUS-direkte-modus (rå bølgjeform), og berre når aktivert i emc.json.
try:
    emc_pusher.start(_emc_hent_vindu)
except Exception as _e:  # noqa: BLE001
    print(f"EMC-skrivar starta ikkje: {_e}")

# Start hub-lager (persistent lagring av push-kanaldata på hubben).
# Skrivartråden er passiv til den vert aktivert i GUI (hub_lager.json).
try:
    hub_lager.start()
except Exception as _e:  # noqa: BLE001
    print(f"Hub-lager starta ikkje: {_e}")

# Start rå-fil-skrivar (arkiverer måledata som CSV til NAS/CIFS).
# Passiv til aktivert i GUI (raa_fil.json).
try:
    import raa_fil_skrivar
    raa_fil_skrivar.start()
except Exception as _e:  # noqa: BLE001
    print(f"Rå-fil-skrivar starta ikkje: {_e}")

# Start modbus-lager (node-side store-and-forward; passiv til aktivert).
# Idempotent — har eigen aktivert-gate. Trygt å kalle i alle modus.
try:
    import modbus_lager
    modbus_lager.start()
except Exception as _e:  # noqa: BLE001
    print(f"Modbus-lager starta ikkje: {_e}")

# Remount NAS frå konfig (om automonter er sett). Bakgrunn — blokkerer ikkje.
try:
    import nas_manager
    nas_manager.start()
except Exception as _e:  # noqa: BLE001
    print(f"NAS-remount starta ikkje: {_e}")


if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)
