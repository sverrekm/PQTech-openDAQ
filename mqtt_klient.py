#!/usr/bin/env python3
"""
MQTT-klient for openDAQ SIRIUS-bro
====================================
Koplar til ein MQTT-broker, abonnerer på konfigurerte topic-ar,
og lagrar siste verdi per topic i ein trådtrygg buffer.

Bruk:
    from mqtt_klient import MqttKlient
    from mqtt_konfig import les_mqtt_konfig

    konfig = les_mqtt_konfig()
    klient = MqttKlient(konfig)
    klient.start()
    verdiar = klient.hent_verdiar()  # {"sensor/temp": 23.5, ...}
    klient.stopp()
"""

import json
import logging
import threading
import time
from typing import Dict, Optional

from mqtt_konfig import MqttKonfig

log = logging.getLogger('mqtt_klient')

try:
    import paho.mqtt.client as mqtt
    PAHO_TILGJENGELEG = True
except ImportError:
    PAHO_TILGJENGELEG = False
    log.warning("paho-mqtt ikkje installert — MQTT-funksjonalitet deaktivert")


def _hent_json_verdi(payload_str: str, json_sti: str) -> Optional[float]:
    """Hent ein numerisk verdi frå ein JSON-streng via dotnotasjon.

    Args:
        payload_str: JSON-streng, t.d. '{"data": {"temp": 23.5}}'
        json_sti: Dot-separert sti, t.d. "data.temp"
                  Tom streng = prøv å parse heile payloaden som tal

    Returns:
        float-verdi eller None viss parsing feilar
    """
    if not json_sti:
        # Prøv heile payloaden som tal
        try:
            return float(payload_str.strip())
        except (ValueError, TypeError):
            pass
        # Prøv som JSON og ta fyrste numeriske verdi
        try:
            obj = json.loads(payload_str)
            if isinstance(obj, (int, float)):
                return float(obj)
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    try:
        obj = json.loads(payload_str)
        for key in json_sti.split('.'):
            if isinstance(obj, dict):
                obj = obj[key]
            elif isinstance(obj, list):
                obj = obj[int(key)]
            else:
                return None
        return float(obj)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        return None


class MqttKlient:
    """MQTT-klient med trådtrygg verdibuffer."""

    def __init__(self, konfig: MqttKonfig):
        self._konfig = konfig
        self._client: Optional['mqtt.Client'] = None
        self._verdiar: Dict[str, float] = {}    # topic → siste verdi
        self._tidsstempel: Dict[str, float] = {}  # topic → tidspunkt
        self._lock = threading.Lock()
        self._tilkobla = False
        self._feil: Optional[str] = None
        self._meldingar_motteke = 0

        # Bygg topic→json_sti-mapping for rask oppslag i on_message
        self._topic_sti: Dict[str, str] = {}
        for k in konfig.kanalar:
            self._topic_sti[k.topic] = k.json_sti

    def start(self):
        """Kopla til broker og start abonnement."""
        if not PAHO_TILGJENGELEG:
            self._feil = "paho-mqtt ikkje installert"
            log.error(self._feil)
            return

        if not self._konfig.broker.aktivert:
            log.info("MQTT deaktivert i konfig")
            return

        if not self._konfig.broker.host:
            self._feil = "MQTT broker host ikkje konfigurert"
            log.warning(self._feil)
            return

        if not self._konfig.kanalar:
            self._feil = "Ingen MQTT-kanalar konfigurert"
            log.warning(self._feil)
            return

        try:
            self._client = mqtt.Client(
                client_id=self._konfig.broker.klient_id,
                protocol=mqtt.MQTTv311,
            )

            if self._konfig.broker.brukarnavn:
                self._client.username_pw_set(
                    self._konfig.broker.brukarnavn,
                    self._konfig.broker.passord or None,
                )

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Automatisk rekonnektering (1-120 sekund delay)
            self._client.reconnect_delay_set(min_delay=1, max_delay=120)

            log.info(f"MQTT: koplar til {self._konfig.broker.host}:"
                     f"{self._konfig.broker.port}...")
            self._client.connect_async(
                self._konfig.broker.host,
                self._konfig.broker.port,
                keepalive=60,
            )
            self._client.loop_start()
            self._feil = None

        except Exception as e:
            self._feil = str(e)
            log.error(f"MQTT tilkobling feilet: {e}")

    def stopp(self):
        """Kopla frå broker og stopp bakgrunnstråd."""
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                log.warning(f"MQTT fråkopling feilet: {e}")
            self._client = None
        self._tilkobla = False
        log.info("MQTT-klient stoppa")

    def restart(self, ny_konfig: MqttKonfig):
        """Stopp, oppdater konfig, og start på nytt."""
        self.stopp()
        self._konfig = ny_konfig
        self._topic_sti = {k.topic: k.json_sti for k in ny_konfig.kanalar}
        with self._lock:
            self._verdiar.clear()
            self._tidsstempel.clear()
            self._meldingar_motteke = 0
        self.start()

    def hent_verdiar(self) -> Dict[str, float]:
        """Hent siste verdiar for alle topics (trådtrygg kopi)."""
        with self._lock:
            return dict(self._verdiar)

    def hent_status(self) -> dict:
        """Hent MQTT-klientstatus for web API."""
        with self._lock:
            topic_status = {}
            for k in self._konfig.kanalar:
                topic_status[k.topic] = {
                    "namn": k.namn,
                    "enhet": k.enhet,
                    "verdi": self._verdiar.get(k.topic),
                    "sist_oppdatert": self._tidsstempel.get(k.topic),
                }
        return {
            "tilkobla": self._tilkobla,
            "aktivert": self._konfig.broker.aktivert,
            "broker": f"{self._konfig.broker.host}:{self._konfig.broker.port}",
            "feil": self._feil,
            "meldingar_motteke": self._meldingar_motteke,
            "topics": topic_status,
        }

    # --- paho callbacks ---

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._tilkobla = True
            self._feil = None
            topics = [(k.topic, 0) for k in self._konfig.kanalar]
            if topics:
                client.subscribe(topics)
                log.info(f"MQTT tilkobla, abonnerer på {len(topics)} topics")
            else:
                log.info("MQTT tilkobla (ingen topics)")
        else:
            rc_tekst = {
                1: "Feil protokollversjon",
                2: "Ugyldig klient-ID",
                3: "Server utilgjengeleg",
                4: "Feil brukarnavn/passord",
                5: "Ikkje autorisert",
            }
            self._feil = rc_tekst.get(rc, f"Feilkode {rc}")
            self._tilkobla = False
            log.warning(f"MQTT tilkobling avvist: {self._feil}")

    def _on_disconnect(self, client, userdata, rc):
        self._tilkobla = False
        if rc != 0:
            self._feil = f"Uventa fråkopling (rc={rc})"
            log.warning(f"MQTT fråkobla uventa (rc={rc}), prøver rekonnektering...")
        else:
            log.info("MQTT fråkobla")

    def _on_message(self, client, userdata, msg):
        try:
            payload_str = msg.payload.decode('utf-8', errors='replace')
            json_sti = self._topic_sti.get(msg.topic, "")
            verdi = _hent_json_verdi(payload_str, json_sti)

            if verdi is not None:
                now = time.time()
                with self._lock:
                    self._verdiar[msg.topic] = verdi
                    self._tidsstempel[msg.topic] = now
                    self._meldingar_motteke += 1
            else:
                # Logg berre fyrste gong per topic
                if msg.topic not in self._verdiar:
                    log.warning(f"MQTT {msg.topic}: kunne ikkje parse verdi "
                                f"frå '{payload_str[:100]}' "
                                f"(json_sti='{json_sti}')")
        except Exception as e:
            log.warning(f"MQTT melding feil: {e}")
