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
HUB_KANAL_RANGE_STI = Path("/data/konfig/hub_kanal_ranges.json")


NODE_TYPE_OPENDAQ = "opendaq"
NODE_TYPE_MODBUS_TCP = "modbus_tcp"
NODE_TYPAR = (NODE_TYPE_OPENDAQ, NODE_TYPE_MODBUS_TCP)

MODBUS_FUNKSJONAR = ("holding", "input", "coil", "discrete")
MODBUS_DATATYPAR = ("int16", "uint16", "int32", "uint32", "float32")
MODBUS_BYTE_ORDERS = ("AB_CD", "CD_AB", "BA_DC", "DC_BA")


@dataclass
class ModbusRegister:
    """Eit Modbus-register som vert ein kanal i hubben."""
    namn: str
    adresse: int
    funksjon: str = "holding"      # holding | input | coil | discrete
    datatype: str = "float32"      # int16 | uint16 | int32 | uint32 | float32
    byte_order: str = "AB_CD"      # AB_CD (big) | CD_AB (mid-swap) | BA_DC | DC_BA
    skalering: float = 1.0
    offset: float = 0.0
    eining: str = ""
    range_low: float = -1000.0
    range_high: float = 1000.0
    # Når True: registeret vert IKKJE bygd som openDAQ-kanal (sparer den tunge
    # 20kHz-strøymen over WAN), men modbus_manager les det og ein lett
    # forwarder sender verdien som line-protocol til hubben (→ InfluxDB).
    # Brukt for mange skalar-verdiar som harmoniske.
    forward_berre: bool = False

    def til_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fraa_dict(cls, d: dict) -> 'ModbusRegister':
        return cls(
            namn=str(d.get("namn", "")),
            adresse=int(d.get("adresse", 0)),
            funksjon=str(d.get("funksjon", "holding")),
            datatype=str(d.get("datatype", "float32")),
            byte_order=str(d.get("byte_order", "AB_CD")),
            skalering=float(d.get("skalering", 1.0)),
            offset=float(d.get("offset", 0.0)),
            eining=str(d.get("eining", "")),
            range_low=float(d.get("range_low", -1000.0)),
            range_high=float(d.get("range_high", 1000.0)),
            forward_berre=bool(d.get("forward_berre", False)),
        )


@dataclass
class FjernNode:
    """Ein fjern-node som hubben koplar til (openDAQ eller Modbus TCP)."""
    id: str                     # Unikt ID (auto UUID[:8])
    namn: str                   # "Sundet - Tavle 3"
    adresse: str                # IP-adresse, t.d. "10.0.0.5" eller "192.168.1.81"
    port: int = 4840
    aktivert: bool = True
    protokoll: str = "daq.opcua"  # OPC-UA (for type=opendaq). Ignorert for modbus.
    lokasjon: str = ""
    type: str = NODE_TYPE_OPENDAQ  # "opendaq" | "modbus_tcp"

    # Modbus-spesifikke felt (berre i bruk når type=modbus_tcp)
    modbus_unit_id: int = 1
    modbus_poll_hz: float = 1.0
    modbus_timeout_ms: int = 2000
    modbus_base_adresse: int = 0   # Vert lagt til reg.adresse ved lesing. 0 = reg.adresse er absolutt.
    modbus_registers: List[ModbusRegister] = field(default_factory=list)

    def til_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fraa_dict(cls, d: dict) -> 'FjernNode':
        node_type = str(d.get("type", NODE_TYPE_OPENDAQ))
        default_port = 502 if node_type == NODE_TYPE_MODBUS_TCP else 7420
        registers_data = d.get("modbus_registers", []) or []
        registers = [ModbusRegister.fraa_dict(r) for r in registers_data]
        return cls(
            id=str(d.get("id", uuid.uuid4().hex[:8])),
            namn=str(d.get("namn", "")),
            adresse=str(d.get("adresse", "")),
            port=int(d.get("port", default_port)),
            aktivert=bool(d.get("aktivert", True)),
            protokoll=str(d.get("protokoll", "daq.nd")),
            lokasjon=str(d.get("lokasjon", "")),
            type=node_type,
            modbus_unit_id=int(d.get("modbus_unit_id", 1)),
            modbus_poll_hz=float(d.get("modbus_poll_hz", 1.0)),
            modbus_timeout_ms=int(d.get("modbus_timeout_ms", 2000)),
            modbus_base_adresse=int(d.get("modbus_base_adresse", 0)),
            modbus_registers=registers,
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
                if node.type == NODE_TYPE_OPENDAQ and node.protokoll == "daq.nd":
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


@dataclass
class KanalRangeOverstyring:
    """Brukar-overstyrt range for ein hub-kanal."""
    node_id: str
    kanal_namn: str
    range_low: float
    range_high: float
    aktiv: bool = True

    def til_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fraa_dict(cls, d: dict) -> 'KanalRangeOverstyring':
        return cls(
            node_id=str(d.get("node_id", "")),
            kanal_namn=str(d.get("kanal_namn", "")),
            range_low=float(d.get("range_low", -1000)),
            range_high=float(d.get("range_high", 1000)),
            aktiv=bool(d.get("aktiv", True)),
        )


def les_kanal_ranges() -> List[KanalRangeOverstyring]:
    """Les kanal-range overstyringer frå JSON-fil."""
    try:
        if HUB_KANAL_RANGE_STI.exists():
            raa = json.loads(HUB_KANAL_RANGE_STI.read_text(encoding='utf-8'))
            overstyringer = [KanalRangeOverstyring.fraa_dict(o) for o in raa]
            log.info(f"Lasta {len(overstyringer)} kanal-range overstyringer")
            return overstyringer
    except Exception as e:
        log.warning(f"Kunne ikkje lese kanal-ranges: {e}")
    return []


def lagre_kanal_ranges(overstyringer: List[KanalRangeOverstyring]) -> bool:
    """Lagre kanal-range overstyringer til JSON-fil."""
    try:
        HUB_KANAL_RANGE_STI.parent.mkdir(parents=True, exist_ok=True)
        HUB_KANAL_RANGE_STI.write_text(
            json.dumps([o.til_dict() for o in overstyringer], indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        log.info(f"Lagra {len(overstyringer)} kanal-range overstyringer")
        return True
    except Exception as e:
        log.error(f"Kunne ikkje lagre kanal-ranges: {e}")
        return False


def hent_range_map(overstyringer: List[KanalRangeOverstyring]) -> dict:
    """Bygg oppslag: 'node_id:kanal_namn' -> (range_low, range_high) for aktive overstyringer."""
    return {
        f"{o.node_id}:{o.kanal_namn}": (o.range_low, o.range_high)
        for o in overstyringer if o.aktiv
    }


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

        node_type = str(n.get("type", NODE_TYPE_OPENDAQ)).strip()
        if node_type not in NODE_TYPAR:
            return None, f"Node {i}: ugyldig type '{node_type}' ({'/'.join(NODE_TYPAR)})"

        default_port = 502 if node_type == NODE_TYPE_MODBUS_TCP else 4840
        port = n.get("port", default_port)
        try:
            port = int(port)
        except (TypeError, ValueError):
            return None, f"Node {i}: ugyldig port: {port}"
        if port < 1 or port > 65535:
            return None, f"Node {i}: port {port} utanfor gyldig område (1-65535)"

        protokoll = str(n.get("protokoll", "daq.opcua")).strip()
        if node_type == NODE_TYPE_OPENDAQ and protokoll not in ("daq.opcua", "daq.nd"):
            return None, f"Node {i}: ugyldig protokoll '{protokoll}' (daq.opcua eller daq.nd)"

        # Modbus-spesifikk validering
        modbus_unit_id = 1
        modbus_poll_hz = 1.0
        modbus_timeout_ms = 2000
        modbus_base_adresse = 0
        modbus_registers: List[ModbusRegister] = []
        if node_type == NODE_TYPE_MODBUS_TCP:
            try:
                modbus_unit_id = int(n.get("modbus_unit_id", 1))
            except (TypeError, ValueError):
                return None, f"Node {i}: ugyldig modbus_unit_id"
            if modbus_unit_id < 0 or modbus_unit_id > 255:
                return None, f"Node {i}: modbus_unit_id {modbus_unit_id} utanfor 0-255"

            try:
                modbus_poll_hz = float(n.get("modbus_poll_hz", 1.0))
            except (TypeError, ValueError):
                return None, f"Node {i}: ugyldig modbus_poll_hz"
            if modbus_poll_hz < 0.1 or modbus_poll_hz > 100.0:
                return None, f"Node {i}: modbus_poll_hz {modbus_poll_hz} utanfor 0.1-100"

            try:
                modbus_timeout_ms = int(n.get("modbus_timeout_ms", 2000))
            except (TypeError, ValueError):
                return None, f"Node {i}: ugyldig modbus_timeout_ms"
            if modbus_timeout_ms < 100 or modbus_timeout_ms > 30000:
                return None, f"Node {i}: modbus_timeout_ms {modbus_timeout_ms} utanfor 100-30000"

            try:
                modbus_base_adresse = int(n.get("modbus_base_adresse", 0))
            except (TypeError, ValueError):
                return None, f"Node {i}: ugyldig modbus_base_adresse"
            if modbus_base_adresse < 0 or modbus_base_adresse > 65535:
                return None, f"Node {i}: modbus_base_adresse {modbus_base_adresse} utanfor 0-65535"

            regs_data = n.get("modbus_registers", []) or []
            if not isinstance(regs_data, list):
                return None, f"Node {i}: 'modbus_registers' må vere ei liste"
            if len(regs_data) > 256:
                return None, f"Node {i}: maks 256 register (fekk {len(regs_data)})"

            reg_namn_set = set()
            for ri, r in enumerate(regs_data):
                if not isinstance(r, dict):
                    return None, f"Node {i} register {ri}: forventa objekt"
                r_namn = str(r.get("namn", "")).strip()
                if not r_namn:
                    return None, f"Node {i} register {ri}: 'namn' kan ikkje vere tom"
                if r_namn in reg_namn_set:
                    return None, f"Node {i} register {ri}: duplikat namn '{r_namn}'"
                reg_namn_set.add(r_namn)

                try:
                    r_adr = int(r.get("adresse", 0))
                except (TypeError, ValueError):
                    return None, f"Node {i} register {ri}: ugyldig adresse"
                if r_adr < 0 or r_adr > 65535:
                    return None, f"Node {i} register {ri}: adresse {r_adr} utanfor 0-65535"

                r_fn = str(r.get("funksjon", "holding"))
                if r_fn not in MODBUS_FUNKSJONAR:
                    return None, f"Node {i} register {ri}: ugyldig funksjon '{r_fn}' ({'/'.join(MODBUS_FUNKSJONAR)})"

                r_dt = str(r.get("datatype", "float32"))
                if r_dt not in MODBUS_DATATYPAR:
                    return None, f"Node {i} register {ri}: ugyldig datatype '{r_dt}' ({'/'.join(MODBUS_DATATYPAR)})"

                r_bo = str(r.get("byte_order", "AB_CD"))
                if r_bo not in MODBUS_BYTE_ORDERS:
                    return None, f"Node {i} register {ri}: ugyldig byte_order '{r_bo}' ({'/'.join(MODBUS_BYTE_ORDERS)})"

                try:
                    r_low = float(r.get("range_low", -1000.0))
                    r_high = float(r.get("range_high", 1000.0))
                except (TypeError, ValueError):
                    return None, f"Node {i} register {ri}: ugyldig range-verdi"
                if r_low >= r_high:
                    return None, f"Node {i} register {ri}: range_low ({r_low}) må vere mindre enn range_high ({r_high})"

                try:
                    r_scale = float(r.get("skalering", 1.0))
                    r_offset = float(r.get("offset", 0.0))
                except (TypeError, ValueError):
                    return None, f"Node {i} register {ri}: ugyldig skalering/offset"

                modbus_registers.append(ModbusRegister(
                    namn=r_namn,
                    adresse=r_adr,
                    funksjon=r_fn,
                    datatype=r_dt,
                    byte_order=r_bo,
                    skalering=r_scale,
                    offset=r_offset,
                    eining=str(r.get("eining", "")),
                    range_low=r_low,
                    range_high=r_high,
                ))

        nodar.append(FjernNode(
            id=str(n.get("id", uuid.uuid4().hex[:8])),
            namn=namn,
            adresse=adresse,
            port=port,
            aktivert=bool(n.get("aktivert", True)),
            protokoll=protokoll,
            lokasjon=str(n.get("lokasjon", "")),
            type=node_type,
            modbus_unit_id=modbus_unit_id,
            modbus_poll_hz=modbus_poll_hz,
            modbus_timeout_ms=modbus_timeout_ms,
            modbus_base_adresse=modbus_base_adresse,
            modbus_registers=modbus_registers,
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
