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
import subprocess
import socket
import threading
import glob as glob_mod

from flask import Flask, jsonify, request, session, send_file

import usbip_manager
import tailscale_manager
import oppdatering
import brukar_auth

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
    )
    SIRIUS_DIREKTE = True
except ImportError:
    SIRIUS_DIREKTE = False

from kanal_konfig import KanalKonfig, les_konfig, lagre_konfig, valider_konfig, STANDARD_KONFIG
from mqtt_konfig import valider_mqtt_konfig
from enhet_konfig import valider_enhet_konfig, les_modus, lagre_modus, MODUS_DIREKTE, MODUS_USBIP, MODUS_HUB
from buffer_konfig import valider_buffer_konfig, les_buffer_konfig

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
    )

app = Flask(__name__)
brukar_auth.init_app(app)


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
    """Hub-status med per-node info."""
    if HUB_MODUS:
        return jsonify(hent_hub_status())
    # Node-modus: bygg status frå konfig-fil (ikkje live)
    konfig = les_hub_konfig()
    nodar_info = []
    for node in konfig.nodar:
        nodar_info.append({
            "id": node.id,
            "namn": node.namn,
            "adresse": node.adresse,
            "port": node.port,
            "protokoll": node.protokoll,
            "lokasjon": node.lokasjon,
            "aktivert": node.aktivert,
            "tilkobla": False,
            "feil": None,
            "sist_sett": None,
            "tilkobla_sidan": None,
            "antal_kanalar": 0,
        })
    return jsonify({
        "modus": "node",
        "aktiv": False,
        "startet": None,
        "totalt_kanalar": 0,
        "totalt_nodar": len(nodar_info),
        "tilkobla_nodar": 0,
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
    # Node-modus: berre lagre til fil (ingen live tilkoblingar)
    ok = lagre_hub_konfig(konfig)
    return jsonify({
        "suksess": ok,
        "melding": "Konfig lagra (hub ikkje aktiv — vert brukt ved neste hub-oppstart)" if ok
        else "Kunne ikkje lagre konfig"
    })


@app.route("/api/hub/nodar", methods=["POST"])
def api_hub_legg_til_node():
    """Legg til ein ny fjern-node."""
    data = request.get_json(silent=True) or {}
    if HUB_MODUS:
        ok, melding, node = legg_til_node_api(data)
        result = {"suksess": ok, "melding": melding}
        if node:
            result["node"] = node
        return jsonify(result), 200 if ok else 400
    # Node-modus: legg til i konfig-fil
    import uuid as _uuid
    adresse = str(data.get("adresse", "")).strip()
    if not adresse:
        return jsonify({"suksess": False, "melding": "Mangler 'adresse'"}), 400
    namn = str(data.get("namn", "")).strip() or adresse
    node = FjernNode(
        id=_uuid.uuid4().hex[:8],
        namn=namn,
        adresse=adresse,
        port=int(data.get("port", 4840)),
        aktivert=True,
        protokoll=str(data.get("protokoll", "daq.opcua")),
        lokasjon=str(data.get("lokasjon", "")),
    )
    konfig = les_hub_konfig()
    konfig.nodar.append(node)
    ok = lagre_hub_konfig(konfig)
    return jsonify({
        "suksess": ok,
        "melding": f"Node '{namn}' lagt til (hub ikkje aktiv)" if ok else "Lagring feila",
        "node": node.til_dict() if ok else None,
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
    return jsonify({"suksess": ok, "melding": f"Node '{node.namn}' fjerna"})


@app.route("/api/hub/nodar/<node_id>/rekoble", methods=["POST"])
def api_hub_rekoble_node(node_id):
    """Tving rekobling av ein node."""
    if not HUB_MODUS:
        return jsonify({"suksess": False, "melding": "Hub ikkje aktiv — start med OPENDAQ_MODUS=hub"})
    ok, melding = rekoble_node(node_id)
    return jsonify({"suksess": ok, "melding": melding})


@app.route("/api/hub/kanalar")
def api_hub_kanalar():
    """Kanal-metadata og live-verdiar frå tilkobla nodar."""
    if not HUB_MODUS:
        return jsonify({"kanalar": []})
    try:
        return jsonify({"kanalar": hent_hub_kanalar()})
    except Exception as e:
        return jsonify({"kanalar": [], "feil": str(e)})


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


@app.route("/api/system/oppdater", methods=["POST"])
def api_system_oppdater():
    """Last ned og installer oppdatering frå GitHub, deretter restart."""
    try:
        resultat = oppdatering.last_ned_og_oppdater()
        # Planlegg restart etter at responsen er sendt
        threading.Timer(2.0, lambda: os._exit(0)).start()
        return jsonify(resultat)
    except Exception as e:
        return jsonify({"suksess": False, "feil": str(e)}), 500


@app.route("/api/system/restart", methods=["POST"])
def api_system_restart():
    """Restart containeren (os._exit → Docker restart-policy startar på nytt)."""
    log.info("Restart forespurt via API — avsluttar om 2 sek...")
    threading.Timer(2.0, lambda: os._exit(0)).start()
    return jsonify({"suksess": True, "melding": "Omstart om 2 sekund..."})


# --- Buffer API ---

@app.route("/api/buffer/status")
def api_buffer_status():
    if SIRIUS_DIREKTE:
        return jsonify(_buffer_hent_status())
    return jsonify({"aktivert": False, "totalt_rader": 0})


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


@app.route("/")
def index():
    return send_file(os.path.join(FRONTEND_DIR, "index.html"))


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
    return send_file(os.path.join(FRONTEND_DIR, "index.html"))


# --- Legacy HTML removed (migrated to React in frontend/) ---


if __name__ == "__main__":
    port = int(os.environ.get("WEB_PORT", 8080))
    app.run(host="0.0.0.0", port=port)
