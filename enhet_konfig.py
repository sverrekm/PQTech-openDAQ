#!/usr/bin/env python3
"""
Enheitskonfigurasjon for SIRIUS openDAQ-bro
=============================================
Lagrar enheits-innstillingar som antal ADC-kanalar, modell, etc.
Persistert til /data/konfig/enhet.json (Docker-volume).

Bruk:
    from enhet_konfig import les_enhet_konfig, lagre_enhet_konfig
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

log = logging.getLogger('enhet_konfig')

ENHET_KONFIG_STI = Path("/data/konfig/enhet.json")


@dataclass
class EnhetKonfig:
    """Konfigurasjon for SIRIUS-enheten."""
    antal_adc_kanalar: int = 8       # 4, 8, 16 etc. avhengig av SIRIUS-modell
    modell: str = "SIRIUSi-HS-8xLV"  # Modellnamn for visning


def les_enhet_konfig() -> EnhetKonfig:
    """Les enheitskonfig frå JSON-fil. Returnerer standard viss fila ikkje finst."""
    try:
        if ENHET_KONFIG_STI.exists():
            data = json.loads(ENHET_KONFIG_STI.read_text(encoding='utf-8'))
            konfig = EnhetKonfig(
                antal_adc_kanalar=int(data.get("antal_adc_kanalar", 8)),
                modell=str(data.get("modell", "SIRIUSi-HS-8xLV")),
            )
            log.info(f"Lasta enhet-konfig: {konfig.antal_adc_kanalar} ADC-kanalar, "
                     f"modell={konfig.modell}")
            return konfig
    except Exception as e:
        log.warning(f"Kunne ikkje lese enhet-konfig: {e}")
    return EnhetKonfig()


def lagre_enhet_konfig(konfig: EnhetKonfig) -> bool:
    """Lagre enheitskonfig til JSON-fil."""
    try:
        ENHET_KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        ENHET_KONFIG_STI.write_text(
            json.dumps(asdict(konfig), indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        log.info(f"Lagra enhet-konfig: {konfig.antal_adc_kanalar} ADC, "
                 f"modell={konfig.modell}")
        return True
    except Exception as e:
        log.error(f"Kunne ikkje lagre enhet-konfig: {e}")
        return False


def valider_enhet_konfig(data: dict) -> tuple:
    """Valider enheit-konfig frå API-input.

    Returns:
        (EnhetKonfig, feilmelding) - konfig er None viss validering feilar
    """
    if not isinstance(data, dict):
        return None, "Forventa eit objekt"

    try:
        n = int(data.get("antal_adc_kanalar", 8))
    except (TypeError, ValueError):
        return None, "antal_adc_kanalar maa vere eit heiltal"

    if n < 1 or n > 32:
        return None, f"antal_adc_kanalar {n} utanfor gyldig omraade (1-32)"

    modell = str(data.get("modell", "SIRIUSi-HS-8xLV"))

    return EnhetKonfig(antal_adc_kanalar=n, modell=modell), None
