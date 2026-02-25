#!/usr/bin/env python3
"""
MQTT-konfigurasjon for openDAQ SIRIUS-bro
==========================================
Datamodell og JSON-persistens for MQTT broker-tilkobling
og topic-til-kanal-mapping.

Lagrar til /data/konfig/mqtt.json (montert som Docker-volume).

Bruk:
    from mqtt_konfig import MqttKonfig, les_mqtt_konfig, lagre_mqtt_konfig
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

log = logging.getLogger('mqtt_konfig')

MQTT_KONFIG_STI = Path("/data/konfig/mqtt.json")


@dataclass
class MqttBrokerKonfig:
    """Tilkoblingsinnstillingar for MQTT-brokeren."""
    host: str = ""
    port: int = 1883
    brukarnavn: str = ""
    passord: str = ""
    klient_id: str = "opendaq-sirius"
    aktivert: bool = False

    def til_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fraa_dict(cls, d: dict) -> 'MqttBrokerKonfig':
        return cls(
            host=str(d.get("host", "")),
            port=int(d.get("port", 1883)),
            brukarnavn=str(d.get("brukarnavn", "")),
            passord=str(d.get("passord", "")),
            klient_id=str(d.get("klient_id", "opendaq-sirius")),
            aktivert=bool(d.get("aktivert", False)),
        )


@dataclass
class MqttKanalKonfig:
    """Mapping frå MQTT-topic til virtuell kanal."""
    topic: str                  # "sensor/temperatur"
    namn: str                   # "Temperatur ute"
    enhet: str = ""             # "°C", "bar", "m/s"
    json_sti: str = ""          # "value", "data.temp" — tom = heile payloaden
    range_min: float = -1000.0
    range_max: float = 1000.0

    def til_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fraa_dict(cls, d: dict) -> 'MqttKanalKonfig':
        return cls(
            topic=str(d.get("topic", "")),
            namn=str(d.get("namn", "")),
            enhet=str(d.get("enhet", "")),
            json_sti=str(d.get("json_sti", "")),
            range_min=float(d.get("range_min", -1000.0)),
            range_max=float(d.get("range_max", 1000.0)),
        )


@dataclass
class MqttKonfig:
    """Komplett MQTT-konfigurasjon (broker + kanalar)."""
    broker: MqttBrokerKonfig = field(default_factory=MqttBrokerKonfig)
    kanalar: List[MqttKanalKonfig] = field(default_factory=list)

    def til_dict(self) -> dict:
        return {
            "broker": self.broker.til_dict(),
            "kanalar": [k.til_dict() for k in self.kanalar],
        }

    @classmethod
    def fraa_dict(cls, d: dict) -> 'MqttKonfig':
        broker = MqttBrokerKonfig.fraa_dict(d.get("broker", {}))
        kanalar = [MqttKanalKonfig.fraa_dict(k) for k in d.get("kanalar", [])]
        return cls(broker=broker, kanalar=kanalar)


def les_mqtt_konfig() -> MqttKonfig:
    """Les MQTT-konfigurasjon frå JSON-fil. Returnerer tom konfig viss fila ikkje finst."""
    try:
        if MQTT_KONFIG_STI.exists():
            raa = json.loads(MQTT_KONFIG_STI.read_text(encoding='utf-8'))
            konfig = MqttKonfig.fraa_dict(raa)
            log.info(f"Lasta MQTT-konfig fraa {MQTT_KONFIG_STI}: "
                     f"broker={konfig.broker.host}:{konfig.broker.port}, "
                     f"{len(konfig.kanalar)} kanalar")
            return konfig
    except Exception as e:
        log.warning(f"Kunne ikkje lese MQTT-konfig: {e}")
    return MqttKonfig()


def lagre_mqtt_konfig(konfig: MqttKonfig) -> bool:
    """Lagre MQTT-konfigurasjon til JSON-fil."""
    try:
        MQTT_KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        MQTT_KONFIG_STI.write_text(
            json.dumps(konfig.til_dict(), indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        log.info(f"Lagra MQTT-konfig til {MQTT_KONFIG_STI}: "
                 f"broker={konfig.broker.host}:{konfig.broker.port}, "
                 f"{len(konfig.kanalar)} kanalar")
        return True
    except Exception as e:
        log.error(f"Kunne ikkje lagre MQTT-konfig: {e}")
        return False


def valider_mqtt_konfig(data: dict) -> tuple:
    """
    Valider MQTT-konfig frå API-input.

    Returns:
        (MqttKonfig, feilmelding) — konfig er None viss validering feilar
    """
    if not isinstance(data, dict):
        return None, "Forventa eit objekt med 'broker' og 'kanalar'"

    broker_data = data.get("broker", {})
    if not isinstance(broker_data, dict):
        return None, "'broker' må vere eit objekt"

    host = str(broker_data.get("host", "")).strip()
    port = broker_data.get("port", 1883)
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None, f"Ugyldig port: {port}"
    if port < 1 or port > 65535:
        return None, f"Port {port} utanfor gyldig område (1-65535)"

    kanalar_data = data.get("kanalar", [])
    if not isinstance(kanalar_data, list):
        return None, "'kanalar' må vere ei liste"

    if len(kanalar_data) > 32:
        return None, f"Maks 32 MQTT-kanalar (fekk {len(kanalar_data)})"

    kanalar = []
    for i, k in enumerate(kanalar_data):
        if not isinstance(k, dict):
            return None, f"Kanal {i}: forventa objekt"
        topic = str(k.get("topic", "")).strip()
        if not topic:
            return None, f"Kanal {i}: 'topic' kan ikkje vere tom"
        namn = str(k.get("namn", "")).strip() or topic
        kanalar.append(MqttKanalKonfig(
            topic=topic,
            namn=namn,
            enhet=str(k.get("enhet", "")),
            json_sti=str(k.get("json_sti", "")),
            range_min=float(k.get("range_min", -1000.0)),
            range_max=float(k.get("range_max", 1000.0)),
        ))

    konfig = MqttKonfig(
        broker=MqttBrokerKonfig(
            host=host,
            port=port,
            brukarnavn=str(broker_data.get("brukarnavn", "")),
            passord=str(broker_data.get("passord", "")),
            klient_id=str(broker_data.get("klient_id", "opendaq-sirius")).strip() or "opendaq-sirius",
            aktivert=bool(broker_data.get("aktivert", False)),
        ),
        kanalar=kanalar,
    )
    return konfig, None
