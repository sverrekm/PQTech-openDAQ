#!/usr/bin/env python3
"""
Buffer-konfigurasjon for måledata-ringbuffer
=============================================
Lagrar innstillingar for lokal SQLite-buffer og hub-synkronisering.
Persistert til /data/konfig/buffer.json (Docker-volume).

Bruk:
    from buffer_konfig import les_buffer_konfig, lagre_buffer_konfig
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

log = logging.getLogger('buffer_konfig')

BUFFER_KONFIG_STI = Path("/data/konfig/buffer.json")


@dataclass
class BufferKonfig:
    """Konfigurasjon for måledata-ringbuffer."""
    aktivert: bool = True               # Buffer aktiv/inaktiv
    intervall_ms: int = 100             # Aggregeringsvindu i ms (100ms = 10 Hz)
    maks_storleik_mb: int = 2048        # Maks SQLite-storleik i MB
    bevar_usynkronisert: bool = True    # Aldri slett usynkroniserte rader
    hub_sync_intervall_sek: int = 60    # Hub pollar kvart N sekund
    hub_batch_storleik: int = 10000     # Rader per sync-batch
    hub_retensjon_dagar: int = 30       # Hub lagrar N dagar


def les_buffer_konfig() -> BufferKonfig:
    """Les buffer-konfig frå JSON-fil. Returnerer standard viss fila ikkje finst."""
    try:
        if BUFFER_KONFIG_STI.exists():
            data = json.loads(BUFFER_KONFIG_STI.read_text(encoding='utf-8'))
            konfig = BufferKonfig(
                aktivert=bool(data.get("aktivert", True)),
                intervall_ms=int(data.get("intervall_ms", 100)),
                maks_storleik_mb=int(data.get("maks_storleik_mb", 2048)),
                bevar_usynkronisert=bool(data.get("bevar_usynkronisert", True)),
                hub_sync_intervall_sek=int(data.get("hub_sync_intervall_sek", 60)),
                hub_batch_storleik=int(data.get("hub_batch_storleik", 10000)),
                hub_retensjon_dagar=int(data.get("hub_retensjon_dagar", 30)),
            )
            log.info(f"Lasta buffer-konfig: aktivert={konfig.aktivert}, "
                     f"intervall={konfig.intervall_ms}ms, "
                     f"maks={konfig.maks_storleik_mb}MB")
            return konfig
    except Exception as e:
        log.warning(f"Kunne ikkje lese buffer-konfig: {e}")
    return BufferKonfig()


def lagre_buffer_konfig(konfig: BufferKonfig) -> bool:
    """Lagre buffer-konfig til JSON-fil."""
    try:
        BUFFER_KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        BUFFER_KONFIG_STI.write_text(
            json.dumps(asdict(konfig), indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        log.info(f"Lagra buffer-konfig: aktivert={konfig.aktivert}, "
                 f"maks={konfig.maks_storleik_mb}MB")
        return True
    except Exception as e:
        log.error(f"Kunne ikkje lagre buffer-konfig: {e}")
        return False


def valider_buffer_konfig(data: dict) -> tuple:
    """Valider buffer-konfig frå API-input.

    Returns:
        (BufferKonfig, feilmelding) - konfig er None viss validering feilar
    """
    if not isinstance(data, dict):
        return None, "Forventa eit objekt"

    try:
        intervall_ms = int(data.get("intervall_ms", 100))
    except (TypeError, ValueError):
        return None, "intervall_ms maa vere eit heiltal"
    if intervall_ms < 10 or intervall_ms > 10000:
        return None, f"intervall_ms {intervall_ms} utanfor gyldig omraade (10-10000)"

    try:
        maks_mb = int(data.get("maks_storleik_mb", 2048))
    except (TypeError, ValueError):
        return None, "maks_storleik_mb maa vere eit heiltal"
    if maks_mb < 100 or maks_mb > 50000:
        return None, f"maks_storleik_mb {maks_mb} utanfor gyldig omraade (100-50000)"

    try:
        sync_intervall = int(data.get("hub_sync_intervall_sek", 60))
    except (TypeError, ValueError):
        return None, "hub_sync_intervall_sek maa vere eit heiltal"
    if sync_intervall < 5 or sync_intervall > 3600:
        return None, f"hub_sync_intervall_sek {sync_intervall} utanfor gyldig omraade (5-3600)"

    try:
        batch = int(data.get("hub_batch_storleik", 10000))
    except (TypeError, ValueError):
        return None, "hub_batch_storleik maa vere eit heiltal"
    if batch < 100 or batch > 100000:
        return None, f"hub_batch_storleik {batch} utanfor gyldig omraade (100-100000)"

    try:
        retensjon = int(data.get("hub_retensjon_dagar", 30))
    except (TypeError, ValueError):
        return None, "hub_retensjon_dagar maa vere eit heiltal"
    if retensjon < 1 or retensjon > 365:
        return None, f"hub_retensjon_dagar {retensjon} utanfor gyldig omraade (1-365)"

    return BufferKonfig(
        aktivert=bool(data.get("aktivert", True)),
        intervall_ms=intervall_ms,
        maks_storleik_mb=maks_mb,
        bevar_usynkronisert=bool(data.get("bevar_usynkronisert", True)),
        hub_sync_intervall_sek=sync_intervall,
        hub_batch_storleik=batch,
        hub_retensjon_dagar=retensjon,
    ), None
