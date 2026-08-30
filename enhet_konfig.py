#!/usr/bin/env python3
"""
Enheitskonfigurasjon for Dewesoft openDAQ-bro
==============================================
Lagrar enheits-innstillingar som antal ADC-kanalar, modell, etc.
Persistert til /data/konfig/enhet.json (Docker-volume).

Fungerer med alle Dewesoft-instrument (SIRIUS, KRYPTON, IOLITE, MINITAURs, etc.)

Bruk:
    from enhet_konfig import les_enhet_konfig, lagre_enhet_konfig
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

log = logging.getLogger('enhet_konfig')

ENHET_KONFIG_STI = Path("/data/konfig/enhet.json")
MODUS_KONFIG_STI = Path("/data/konfig/modus.json")

# Gyldige driftsmodus
MODUS_DIREKTE = "direkte"   # Container brukar instrumentet direkte over USB
MODUS_USBIP = "usbip"       # Instrumentet delast via USB/IP til Windows
MODUS_HUB = "hub"           # Hub-aggregator for fleire fjern-nodar


@dataclass
class EnhetKonfig:
    """Konfigurasjon for Dewesoft-instrumentet."""
    antal_adc_kanalar: int = 8       # 4, 8, 16 etc. avhengig av modell
    modell: str = ""                 # Auto-detektert eller manuelt sett (tom = ukjend)
    location: str = ""               # Plassering som visest i DewesoftX (t.d. "Verksted", "Tavle 3")
    # openDAQ-rota vert bygd som daqref://device<idx>. Indeksen MÅ vere unik
    # per node under same hub — elles får to nodar same lokale device-ID og
    # hubben kan berre halde den eine («Device with the same local ID already
    # exists»). -1 = ikkje sett her; då gjeld env OPENDAQ_DEVICE_IDX (default 0).
    opendaq_device_idx: int = -1


def les_enhet_konfig() -> EnhetKonfig:
    """Les enheitskonfig frå JSON-fil. Returnerer standard viss fila ikkje finst."""
    try:
        if ENHET_KONFIG_STI.exists():
            data = json.loads(ENHET_KONFIG_STI.read_text(encoding='utf-8'))
            konfig = EnhetKonfig(
                antal_adc_kanalar=int(data.get("antal_adc_kanalar", 8)),
                modell=str(data.get("modell", "")),
                location=str(data.get("location", "")),
                opendaq_device_idx=int(data.get("opendaq_device_idx", -1)),
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

    modell = str(data.get("modell", ""))
    location = str(data.get("location", ""))

    # Device-indeksen vert sett frå eit eige endepunkt/kort. Manglar nøkkelen
    # i input, skal den lagra verdien stå — elles ville kvart lagre frå
    # enhet-kortet nulle ut indeksen og kollisjonen kome tilbake.
    if "opendaq_device_idx" in data:
        try:
            idx = int(data.get("opendaq_device_idx", -1))
        except (TypeError, ValueError):
            return None, "opendaq_device_idx maa vere eit heiltal"
        if idx < -1 or idx > 63:
            return None, f"opendaq_device_idx {idx} utanfor gyldig omraade (-1 til 63)"
    else:
        idx = les_enhet_konfig().opendaq_device_idx

    return EnhetKonfig(antal_adc_kanalar=n, modell=modell, location=location,
                       opendaq_device_idx=idx), None


# --- Modus-persistens ---

def les_modus() -> str:
    """Les sist brukte driftsmodus. Returnerer 'direkte' som standard."""
    try:
        if MODUS_KONFIG_STI.exists():
            data = json.loads(MODUS_KONFIG_STI.read_text(encoding='utf-8'))
            modus = data.get("modus", MODUS_DIREKTE)
            if modus in (MODUS_DIREKTE, MODUS_USBIP, MODUS_HUB):
                log.info(f"Lasta modus: {modus}")
                return modus
            log.warning(f"Ukjend modus '{modus}' i {MODUS_KONFIG_STI}, brukar 'direkte'")
    except Exception as e:
        log.warning(f"Kunne ikkje lese modus: {e}")
    return MODUS_DIREKTE


def lagre_modus(modus: str) -> bool:
    """Lagre gjeldande driftsmodus til fil."""
    if modus not in (MODUS_DIREKTE, MODUS_USBIP, MODUS_HUB):
        log.error(f"Ugyldig modus: {modus}")
        return False
    try:
        MODUS_KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        MODUS_KONFIG_STI.write_text(
            json.dumps({"modus": modus}, indent=2),
            encoding='utf-8'
        )
        log.info(f"Lagra modus: {modus}")
        return True
    except Exception as e:
        log.error(f"Kunne ikkje lagre modus: {e}")
        return False
