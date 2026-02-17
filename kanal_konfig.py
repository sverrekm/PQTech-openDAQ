#!/usr/bin/env python3
"""
Kanal-konfigurasjon for SIRIUS openDAQ-bro
============================================
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
            sample_rate=int(d.get("sample_rate", 1000)),
        )


# Konfig-versjon: Auk denne ved endringar i STANDARD_KONFIG.
# Viss lagra konfig har lågare versjon, vert den erstatta med ny standard.
KONFIG_VERSJON = 6  # v6: Tid-kanal range fiksa (0-3600s, ikkje 0-1M)

# Standard-konfigurasjon for SIRIUSi-HS (4×Hi-LV + 4×Lo-LV)
#
# Kanal 1-4: Hi-LV (høgspenning) — direkte spenningsmåling
#   ADC int16 full-skala = ±1600V (verifisert: RMS 4596 × 1600/32768 ≈ 224V)
#
# Kanal 5-7: Lo-LV (lågspenning) — integrator for straummåling
#   Integrator: 5V-driven, 0-3V utgang → 0-6000A
#   Lo-LV ADC-range: ±5V (anteke)
#   Skalering: faktor = 6000A / 3V = 2000 A/V
#   Kombinert range_max: 5V × 2000 = 10000A
#   Formel: straum_A = raw_int16 × (10000 / 32768)
#
# Kanal 8: Lo-LV, ikkje tilkobla
STANDARD_KONFIG: List[KanalKonfig] = [
    KanalKonfig(0, "AI 0", True,  "voltage", -1600.0, 1600.0, "V", 1000),
    KanalKonfig(1, "AI 1", True,  "voltage", -1600.0, 1600.0, "V", 1000),
    KanalKonfig(2, "AI 2", True,  "voltage", -1600.0, 1600.0, "V", 1000),
    KanalKonfig(3, "AI 3", False, "voltage", -1600.0, 1600.0, "V", 1000),
    KanalKonfig(4, "AI 4", True,  "current", -10000.0, 10000.0, "A", 1000),
    KanalKonfig(5, "AI 5", True,  "current", -10000.0, 10000.0, "A", 1000),
    KanalKonfig(6, "AI 6", True,  "current", -10000.0, 10000.0, "A", 1000),
    KanalKonfig(7, "AI 7", False, "current", -10000.0, 10000.0, "A", 1000),
    KanalKonfig(8, "Tid",  True,  "generic", 0.0, 3600.0, "s", 1000),
]


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

            if isinstance(data, list) and len(data) in (8, 9):
                konfig = [KanalKonfig.fraa_dict(d) for d in data]
                log.info(f"Lasta kanal-konfig v{lagra_versjon} fraa {KONFIG_STI}")
                return konfig
            log.warning(f"Konfig har {len(data)} kanalar (forventa 8), brukar standard")
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
        return None, "Forventa ein liste med 8 kanalar"

    if len(data) != 8:
        return None, f"Forventa 8 kanalar, fekk {len(data)}"

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

        d["indeks"] = i
        konfig.append(KanalKonfig.fraa_dict(d))

    return konfig, None
