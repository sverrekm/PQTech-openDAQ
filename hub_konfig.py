#!/usr/bin/env python3
"""
Hub-konfigurasjon for openDAQ Hub (aggregator)
===============================================
Datamodell og JSON-persistens for fjern-nodar som hubben
koplar til og eksponerer vidare til DewesoftX.

Lagrar til /data/konfig/hub_nodar.json (montert som Docker-volume).

Bruk:
    from hub_konfig import HubKonfig, les_hub_konfig, lagre_hub_konfig
"""

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List

log = logging.getLogger('hub_konfig')

HUB_KONFIG_STI = Path("/data/konfig/hub_nodar.json")


@dataclass
class FjernNode:
    """Ein fjern-node (Pi med SIRIUS) som hubben koplar til."""
    id: str                     # Unikt ID (auto UUID[:8])
    namn: str                   # "Sundet - Tavle 3"
    adresse: str                # Tunnel-IP, t.d. "10.0.0.5"
    port: int = 4840
    aktivert: bool = True
    protokoll: str = "daq.opcua"  # OPC-UA (konfig + auto NativeStreaming), alt: "daq.nd"
    lokasjon: str = ""

    def til_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fraa_dict(cls, d: dict) -> 'FjernNode':
        return cls(
            id=str(d.get("id", uuid.uuid4().hex[:8])),
            namn=str(d.get("namn", "")),
            adresse=str(d.get("adresse", "")),
            port=int(d.get("port", 7420)),
            aktivert=bool(d.get("aktivert", True)),
            protokoll=str(d.get("protokoll", "daq.nd")),
            lokasjon=str(d.get("lokasjon", "")),
        )

    @property
    def tilkobling_streng(self) -> str:
        """Bygg openDAQ tilkoblingsstreng for denne noden."""
        return f"{self.protokoll}://{self.adresse}:{self.port}/"


@dataclass
class HubKonfig:
    """Komplett hub-konfigurasjon (nodar + intervall-innstillingar)."""
    nodar: List[FjernNode] = field(default_factory=list)
    reconnect_intervall: int = 30   # sek
    helsesjekk_intervall: int = 10  # sek

    def til_dict(self) -> dict:
        return {
            "nodar": [n.til_dict() for n in self.nodar],
            "reconnect_intervall": self.reconnect_intervall,
            "helsesjekk_intervall": self.helsesjekk_intervall,
        }

    @classmethod
    def fraa_dict(cls, d: dict) -> 'HubKonfig':
        nodar = [FjernNode.fraa_dict(n) for n in d.get("nodar", [])]
        return cls(
            nodar=nodar,
            reconnect_intervall=int(d.get("reconnect_intervall", 30)),
            helsesjekk_intervall=int(d.get("helsesjekk_intervall", 10)),
        )


def les_hub_konfig() -> HubKonfig:
    """Les hub-konfigurasjon frå JSON-fil. Returnerer tom konfig viss fila ikkje finst."""
    try:
        if HUB_KONFIG_STI.exists():
            raa = json.loads(HUB_KONFIG_STI.read_text(encoding='utf-8'))
            konfig = HubKonfig.fraa_dict(raa)
            log.info(f"Lasta hub-konfig fraa {HUB_KONFIG_STI}: "
                     f"{len(konfig.nodar)} nodar")

            # Migrer frå daq.nd tilbake til daq.opcua.
            # daq.nd:// (NativeConfiguration) er deaktivert på remote-nodar
            # (opendaq_bro.py linje 1592) pga. kompatibilitetsproblem.
            # Hub brukar daq.opcua:// for konfig — openDAQ-klienten
            # oppdagar og koplar til NativeStreaming automatisk for data.
            migrert = False
            for node in konfig.nodar:
                if node.protokoll == "daq.nd":
                    node.protokoll = "daq.opcua"
                    node.port = 4840
                    log.info(f"  Migrert node '{node.namn}': "
                             f"daq.nd → daq.opcua:4840")
                    migrert = True
            if migrert:
                lagre_hub_konfig(konfig)

            return konfig
    except Exception as e:
        log.warning(f"Kunne ikkje lese hub-konfig: {e}")
    return HubKonfig()


def lagre_hub_konfig(konfig: HubKonfig) -> bool:
    """Lagre hub-konfigurasjon til JSON-fil."""
    try:
        HUB_KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        HUB_KONFIG_STI.write_text(
            json.dumps(konfig.til_dict(), indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        log.info(f"Lagra hub-konfig til {HUB_KONFIG_STI}: "
                 f"{len(konfig.nodar)} nodar")
        return True
    except Exception as e:
        log.error(f"Kunne ikkje lagre hub-konfig: {e}")
        return False


def valider_hub_konfig(data: dict) -> tuple:
    """
    Valider hub-konfig frå API-input.

    Returns:
        (HubKonfig, feilmelding) — konfig er None viss validering feilar
    """
    if not isinstance(data, dict):
        return None, "Forventa eit objekt med 'nodar'"

    nodar_data = data.get("nodar", [])
    if not isinstance(nodar_data, list):
        return None, "'nodar' må vere ei liste"

    if len(nodar_data) > 64:
        return None, f"Maks 64 fjern-nodar (fekk {len(nodar_data)})"

    nodar = []
    for i, n in enumerate(nodar_data):
        if not isinstance(n, dict):
            return None, f"Node {i}: forventa objekt"
        adresse = str(n.get("adresse", "")).strip()
        if not adresse:
            return None, f"Node {i}: 'adresse' kan ikkje vere tom"
        namn = str(n.get("namn", "")).strip() or adresse

        port = n.get("port", 4840)
        try:
            port = int(port)
        except (TypeError, ValueError):
            return None, f"Node {i}: ugyldig port: {port}"
        if port < 1 or port > 65535:
            return None, f"Node {i}: port {port} utanfor gyldig område (1-65535)"

        protokoll = str(n.get("protokoll", "daq.nd")).strip()
        if protokoll not in ("daq.opcua", "daq.nd"):
            return None, f"Node {i}: ugyldig protokoll '{protokoll}' (daq.opcua eller daq.nd)"

        nodar.append(FjernNode(
            id=str(n.get("id", uuid.uuid4().hex[:8])),
            namn=namn,
            adresse=adresse,
            port=port,
            aktivert=bool(n.get("aktivert", True)),
            protokoll=protokoll,
            lokasjon=str(n.get("lokasjon", "")),
        ))

    reconnect = data.get("reconnect_intervall", 30)
    helsesjekk = data.get("helsesjekk_intervall", 10)
    try:
        reconnect = max(5, int(reconnect))
        helsesjekk = max(5, int(helsesjekk))
    except (TypeError, ValueError):
        return None, "Ugyldig intervall-verdi"

    konfig = HubKonfig(
        nodar=nodar,
        reconnect_intervall=reconnect,
        helsesjekk_intervall=helsesjekk,
    )
    return konfig, None
