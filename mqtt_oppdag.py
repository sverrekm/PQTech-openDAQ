# -*- coding: utf-8 -*-
"""Avlytt MQTT-brokeren for å finne topics — og gjett kva som er straumdata.

Abonnerer på eit jokerteikn (`#`) ei kort stund og samlar opp kva topics som
finst, kva dei sender, og kva numeriske verdiar som kan trekkjast ut. Topics
(eller enkeltfelt i ein JSON-payload) som ser ut som straumlogging — spenning,
straum, effekt, energi, frekvens … — blir flagga og føreslått som kanalar.

Eigen klient med eige klient-ID, så han ikkje kolliderer med den vanlege
MQTT-klienten som køyrer. Rein stdlib + paho (same som mqtt_klient).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

log = logging.getLogger("mqtt_oppdag")

try:
    import paho.mqtt.client as mqtt
    PAHO = True
except Exception:
    PAHO = False

# Nøkkelord i topic-namn eller JSON-felt → (kvantitet, eining). Rekkjefølgja
# tel: meir spesifikke ord først (energy før power, reactive før power).
_KATEGORIAR: list = [
    (("kwh", "wh", "energy", "energi"), "energy", "Wh"),
    (("var", "reactive", "reaktiv"), "reactive power", "var"),
    (("apparent", "tilsynelat", "kva", "va_"), "apparent power", "VA"),
    (("pf", "cosphi", "cos_phi", "powerfactor", "effektfaktor"), "power factor", ""),
    (("freq", "frekvens", "hz"), "frequency", "Hz"),
    (("volt", "spenning", "voltage", "u_l", "_u_", "vrms", "v_l"), "voltage", "V"),
    (("current", "straum", "strøm", "amp", "ampere", "i_l", "irms", "_i_"), "current", "A"),
    (("power", "effekt", "watt", "_kw", "activepower", "_p_", "p_l"), "power", "W"),
    (("temp", "temperatur"), "temperature", "°C"),
]


def _passar(nokkelord: str, blob: str, tokens: set) -> bool:
    """Match eit nøkkelord mot topic-/felt-teksten.

    Korte, tvetydige ord ("var", "hz", "amp") må matche eit HEILT ord — elles
    slår "var" til på "Varme" og "amp" på "example". Lengre ord og ord med
    skiljeteikn (u_l, cos_phi) får delstreng-match.
    """
    if any(c in nokkelord for c in "_-."):
        return nokkelord in blob
    if len(nokkelord) >= 5:
        return nokkelord in blob
    return nokkelord in tokens


def _klassifiser(*tekstar: str):
    """(interessant, kvantitet, eining) frå topic-/felt-namn."""
    blob = " ".join(t.lower() for t in tekstar if t)
    tokens = set(re.split(r"[^a-z0-9]+", blob))
    for ord_, kvant, eining in _KATEGORIAR:
        if any(_passar(o, blob, tokens) for o in ord_):
            return True, kvant, eining
    return False, "", ""


def _numeriske_stiar(payload: str, maks: int = 40) -> list:
    """[(json_sti, verdi)] frå ein payload. Tom sti = heile payloaden er tal."""
    s = payload.strip()
    # Bar tal
    try:
        return [("", float(s))]
    except (ValueError, TypeError):
        pass
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return []
    ut: list = []

    def gaa(o, prefiks: str):
        if len(ut) >= maks:
            return
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            ut.append((prefiks, float(o)))
        elif isinstance(o, dict):
            for k, v in o.items():
                gaa(v, f"{prefiks}.{k}" if prefiks else str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                gaa(v, f"{prefiks}.{i}" if prefiks else str(i))
    gaa(obj, "")
    return ut


# --- Tilstand --------------------------------------------------------
_lock = threading.Lock()
_funne: dict = {}          # topic -> {tal, sist_payload, sist_ts, stiar: {sti: verdi}}
_tilstand = {"tilstand": "", "starta": 0.0, "varigheit": 0, "melding": "",
             "wildcard": "#"}
_client = None
_stopp = threading.Event()
_traad = None


def _on_connect(client, userdata, flags, rc, *a):
    if rc == 0:
        client.subscribe(_tilstand["wildcard"], qos=0)
        log.info("MQTT-oppdag: abonnerer på %s", _tilstand["wildcard"])
    else:
        with _lock:
            _tilstand["tilstand"] = "feil"
            _tilstand["melding"] = f"Connection refused (rc={rc})"


def _on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8", errors="replace")
    except Exception:
        payload = ""
    now = time.time()
    with _lock:
        f = _funne.get(msg.topic)
        if f is None:
            f = {"tal": 0, "sist_payload": "", "sist_ts": 0.0, "stiar": {}}
            _funne[msg.topic] = f
        f["tal"] += 1
        f["sist_payload"] = payload[:400]
        f["sist_ts"] = now
        for sti, verdi in _numeriske_stiar(payload):
            f["stiar"][sti] = verdi


def start(varigheit: int = 15, wildcard: str = "#") -> dict:
    """Start ei avlytting. Returnerer {suksess, melding}."""
    global _client, _traad
    if not PAHO:
        return {"suksess": False, "melding": "paho-mqtt not installed"}
    from mqtt_konfig import les_mqtt_konfig
    b = les_mqtt_konfig().broker
    if not b.host:
        return {"suksess": False, "melding": "No MQTT broker configured"}
    if _tilstand["tilstand"] == "koeyrer":
        return {"suksess": False, "melding": "A discovery is already running"}

    varigheit = max(3, min(int(varigheit), 120))
    with _lock:
        _funne.clear()
        _tilstand.update(tilstand="koeyrer", starta=time.time(),
                         varigheit=varigheit, melding="", wildcard=wildcard or "#")
    _stopp.clear()

    def kjoer():
        global _client
        try:
            try:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                client_id=(b.klient_id or "opendaq") + "-oppdag")
            except Exception:
                c = mqtt.Client(client_id=(b.klient_id or "opendaq") + "-oppdag")
            if b.brukarnavn:
                c.username_pw_set(b.brukarnavn, b.passord)
            c.on_connect = _on_connect
            c.on_message = _on_message
            _client = c
            c.connect(b.host, int(b.port or 1883), keepalive=30)
            c.loop_start()
            slutt = time.time() + varigheit
            while time.time() < slutt and not _stopp.is_set():
                time.sleep(0.2)
            c.loop_stop()
            try:
                c.disconnect()
            except Exception:
                pass
        except Exception as e:
            with _lock:
                _tilstand["tilstand"] = "feil"
                _tilstand["melding"] = str(e)
            return
        with _lock:
            if _tilstand["tilstand"] != "feil":
                _tilstand["tilstand"] = "ferdig"
                n = len(_funne)
                _tilstand["melding"] = f"{n} {'topic' if n == 1 else 'topics'} found"

    _traad = threading.Thread(target=kjoer, name="mqtt-oppdag", daemon=True)
    _traad.start()
    return {"suksess": True, "melding": f"Listening for {varigheit} s"}


def stopp() -> dict:
    _stopp.set()
    return {"suksess": True, "melding": "Stopping"}


def _forslag_for(topic: str, stiar: dict) -> list:
    """Kvar numerisk sti som eit kanalforslag, straum-data flagga."""
    ut = []
    for sti, verdi in sorted(stiar.items()):
        siste = sti.split(".")[-1] if sti else ""
        interessant, kvant, eining = _klassifiser(topic, siste)
        # Fornuftig standardnamn: topic sitt siste ledd + evt. felt.
        grunn = topic.rstrip("/").split("/")[-1] or topic
        namn = f"{grunn}/{sti}" if sti else grunn
        ut.append({"json_sti": sti, "verdi": verdi, "kvantitet": kvant,
                   "eining": eining, "interessant": interessant, "namn": namn})
    # Interessante først
    ut.sort(key=lambda x: (not x["interessant"], x["json_sti"]))
    return ut


def status() -> dict:
    with _lock:
        att = 0.0
        if _tilstand["tilstand"] == "koeyrer":
            att = max(0.0, _tilstand["starta"] + _tilstand["varigheit"] - time.time())
        topics = []
        for topic, f in _funne.items():
            forslag = _forslag_for(topic, f["stiar"])
            topics.append({
                "topic": topic, "tal": f["tal"],
                "sist_payload": f["sist_payload"],
                "interessant": any(fo["interessant"] for fo in forslag),
                "forslag": forslag,
            })
        topics.sort(key=lambda tpc: (not tpc["interessant"], tpc["topic"]))
        return {"tilstand": _tilstand["tilstand"], "melding": _tilstand["melding"],
                "sekund_att": round(att, 1), "funne": len(topics), "topics": topics}
