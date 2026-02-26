#!/usr/bin/env python3
"""
Kanal-konfigurasjon for Dewesoft openDAQ-bro
=============================================
Datamodell og JSON-persistens for kanalinnstillingar.
Lagrar til /data/konfig/kanalar.json (montert som Docker-volume).

Bruk:
    from kanal_konfig import KanalKonfig, les_konfig, lagre_konfig, STANDARD_KONFIG
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List

log = logging.getLogger('kanal_konfig')

KONFIG_STI = Path("/data/konfig/kanalar.json")

GYLDIGE_TYPAR = ["voltage", "current", "acceleration", "temperature", "generic"]
GYLDIGE_EINHEITAR = ["V", "A", "m/s\u00b2", "\u00b0C", "mV", "mA", ""]


@dataclass
class KanalKonfig:
    """Konfigurasjon for ein enkelt kanal."""
    indeks: int          # 0-7
    namn: str            # "AI 1", "Spenning fase L1", etc.
    aktiv: bool          # True/False
    type: str            # "voltage", "current", "acceleration", etc.
    range_min: float     # -10.0
    range_max: float     # 10.0
    enhet: str           # "V", "A", "m/s2"
    sample_rate: int     # 1000 (Hz)
    # Sensor-skalering (to-punkt lineær)
    sensor_aktiv: bool = False
    sensor_namn: str = ""              # "Rogowski 6kA", "CT 1000A", etc.
    sensor_inn_1: float = 0.0          # Input punkt 1 (V frå forsterkar)
    sensor_ut_1: float = 0.0           # Output punkt 1 (A/fysisk eining)
    sensor_inn_2: float = 1.0          # Input punkt 2 (V)
    sensor_ut_2: float = 1.0           # Output punkt 2 (A)
    sensor_enhet: str = ""             # Output-eining ("A", "kN", etc.)
    # Excitation-spenning (integrator-forsyning frå SIRIUS-forsterkar)
    excitation_v: float = 0.0          # 0.0 = av, 5.0 = 5V unipolar

    def til_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fraa_dict(cls, d: dict) -> 'KanalKonfig':
        return cls(
            indeks=int(d.get("indeks", 0)),
            namn=str(d.get("namn", f"AI {d.get('indeks', 0) + 1}")),
            aktiv=bool(d.get("aktiv", True)),
            type=str(d.get("type", "voltage")),
            range_min=float(d.get("range_min", -10.0)),
            range_max=float(d.get("range_max", 10.0)),
            enhet=str(d.get("enhet", "V")),
            sample_rate=int(d.get("sample_rate", 20000)),
            sensor_aktiv=bool(d.get("sensor_aktiv", False)),
            sensor_namn=str(d.get("sensor_namn", "")),
            sensor_inn_1=float(d.get("sensor_inn_1", 0.0)),
            sensor_ut_1=float(d.get("sensor_ut_1", 0.0)),
            sensor_inn_2=float(d.get("sensor_inn_2", 1.0)),
            sensor_ut_2=float(d.get("sensor_ut_2", 1.0)),
            sensor_enhet=str(d.get("sensor_enhet", "")),
            excitation_v=float(d.get("excitation_v", 0.0)),
        )


# Konfig-versjon: Auk denne ved endringar i STANDARD_KONFIG.
# Viss lagra konfig har lågare versjon, vert den erstatta med ny standard.
KONFIG_VERSJON = 10  # Bump berre ved skjema-endringar, ikkje for nye standardverdiar

# Generisk standard-konfigurasjon — fungerer med alle Dewesoft-instrument.
# Brukarar konfigurerer kanalar via web UI etter at instrumentet er tilkobla.
STANDARD_KONFIG: List[KanalKonfig] = [
    KanalKonfig(0, "AI 0", True,  "voltage", -10.0, 10.0, "V", 20000),
    KanalKonfig(1, "AI 1", True,  "voltage", -10.0, 10.0, "V", 20000),
    KanalKonfig(2, "AI 2", True,  "voltage", -10.0, 10.0, "V", 20000),
    KanalKonfig(3, "AI 3", True,  "voltage", -10.0, 10.0, "V", 20000),
    KanalKonfig(4, "AI 4", True,  "voltage", -10.0, 10.0, "V", 20000),
    KanalKonfig(5, "AI 5", True,  "voltage", -10.0, 10.0, "V", 20000),
    KanalKonfig(6, "AI 6", True,  "voltage", -10.0, 10.0, "V", 20000),
    KanalKonfig(7, "AI 7", True,  "voltage", -10.0, 10.0, "V", 20000),
]


def generer_standard_konfig(antal: int = 8) -> List[KanalKonfig]:
    """Generer standard-konfig for eit gjeve antal ADC-kanalar.

    Brukar STANDARD_KONFIG for dei fyrste 8, deretter genererer
    generiske voltage-kanalar for resten.
    """
    konfig = []
    for i in range(antal):
        if i < len(STANDARD_KONFIG):
            k = KanalKonfig(**asdict(STANDARD_KONFIG[i]))
        else:
            k = KanalKonfig(i, f"AI {i}", False, "voltage",
                            -10.0, 10.0, "V", 20000)
        k.indeks = i
        konfig.append(k)
    return konfig


def les_konfig() -> List[KanalKonfig]:
    """Les kanal-konfigurasjon frå JSON-fil. Returnerer standard viss fila ikkje finst.

    Sjekkar konfig-versjon: viss lagra konfig har lågare versjon enn
    KONFIG_VERSJON, vert den erstatta med ny standard og lagra.
    """
    try:
        if KONFIG_STI.exists():
            raa = json.loads(KONFIG_STI.read_text(encoding='utf-8'))

            # Støtt både gamalt format (liste) og nytt format (dict med versjon)
            if isinstance(raa, dict):
                lagra_versjon = raa.get("versjon", 0)
                data = raa.get("kanalar", [])
            elif isinstance(raa, list):
                lagra_versjon = 0
                data = raa
            else:
                lagra_versjon = 0
                data = []

            # Viss lagra versjon er utdatert, bruk ny standard
            if lagra_versjon < KONFIG_VERSJON:
                log.info(f"Konfig-versjon {lagra_versjon} < {KONFIG_VERSJON}, "
                         f"oppgraderer til ny standard")
                ny = [KanalKonfig(**asdict(k)) for k in STANDARD_KONFIG]
                lagre_konfig(ny)
                return ny

            if isinstance(data, list) and 1 <= len(data) <= 32:
                konfig = [KanalKonfig.fraa_dict(d) for d in data]
                log.info(f"Lasta kanal-konfig v{lagra_versjon} fraa {KONFIG_STI} "
                         f"({len(konfig)} kanalar)")
                return konfig
            log.warning(f"Konfig har {len(data)} kanalar (utanfor 1-32), brukar standard")
    except Exception as e:
        log.warning(f"Kunne ikkje lese kanal-konfig: {e}")

    return [KanalKonfig(**asdict(k)) for k in STANDARD_KONFIG]


def lagre_konfig(konfig: List[KanalKonfig]) -> bool:
    """Lagre kanal-konfigurasjon til JSON-fil (med versjonsnummer)."""
    try:
        KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "versjon": KONFIG_VERSJON,
            "kanalar": [k.til_dict() for k in konfig],
        }
        KONFIG_STI.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        log.info(f"Lagra kanal-konfig v{KONFIG_VERSJON} til {KONFIG_STI}")
        return True
    except Exception as e:
        log.error(f"Kunne ikkje lagre kanal-konfig: {e}")
        return False


def valider_konfig(data: list) -> tuple:
    """
    Valider ein kanal-konfig-liste frå API-input.

    Returns:
        (konfig_liste, feilmelding) - konfig_liste er None viss validering feilar
    """
    if not isinstance(data, list):
        return None, "Forventa ei liste med kanalar"

    if len(data) < 1 or len(data) > 32:
        return None, f"Forventa 1-32 kanalar, fekk {len(data)}"

    konfig = []
    for i, d in enumerate(data):
        if not isinstance(d, dict):
            return None, f"Kanal {i}: forventa objekt"

        # Valider type
        ktype = d.get("type", "voltage")
        if ktype not in GYLDIGE_TYPAR:
            return None, f"Kanal {i}: ugyldig type '{ktype}'"

        # Valider range
        try:
            rmin = float(d.get("range_min", -10.0))
            rmax = float(d.get("range_max", 10.0))
        except (TypeError, ValueError):
            return None, f"Kanal {i}: ugyldig range-verdiar"
        if rmin >= rmax:
            return None, f"Kanal {i}: range_min ({rmin}) maa vere mindre enn range_max ({rmax})"

        # Valider sample_rate
        try:
            sr = int(d.get("sample_rate", 1000))
        except (TypeError, ValueError):
            return None, f"Kanal {i}: ugyldig sample_rate"
        if sr < 1 or sr > 200000:
            return None, f"Kanal {i}: sample_rate {sr} utanfor gyldig omraade (1-200000)"

        # Valider sensor-felt
        sensor_aktiv = d.get("sensor_aktiv", False)
        if not isinstance(sensor_aktiv, bool):
            return None, f"Kanal {i}: sensor_aktiv maa vere bool"
        if sensor_aktiv:
            try:
                s_inn_1 = float(d.get("sensor_inn_1", 0.0))
                s_inn_2 = float(d.get("sensor_inn_2", 1.0))
            except (TypeError, ValueError):
                return None, f"Kanal {i}: ugyldige sensor_inn-verdiar"
            if s_inn_2 == s_inn_1:
                return None, (f"Kanal {i}: sensor_inn_2 ({s_inn_2}) kan ikkje "
                              f"vere lik sensor_inn_1 ({s_inn_1})")
        sensor_enhet = d.get("sensor_enhet", "")
        if not isinstance(sensor_enhet, str):
            return None, f"Kanal {i}: sensor_enhet maa vere streng"

        # Valider excitation
        try:
            exc_v = float(d.get("excitation_v", 0.0))
        except (TypeError, ValueError):
            return None, f"Kanal {i}: ugyldig excitation_v"
        if exc_v not in (0.0, 5.0):
            return None, f"Kanal {i}: excitation_v maa vere 0 eller 5 (fekk {exc_v})"

        d["indeks"] = i
        konfig.append(KanalKonfig.fraa_dict(d))

    return konfig, None
