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

    # Konfigurerbar sample rate (opp til 200 kHz)
    sample_rate: int = 20000
    # SSD-lagringssti (tom = auto-detect /data/ssd)
    ssd_sti: str = ""
    # RAM ringbuffer — antal sekund rå data i minne
    ram_buffer_sekund: int = 30

    # Hendingsdeteksjon
    hendingar_aktivert: bool = True
    rms_terskel_prosent: float = 150.0   # Trigger ved 150% av glidande snitt
    dvdt_terskel: float = 500.0          # dV/dt terskel (V/ms). Over normal
                                         # nett-dV/dt (~100) for å unngå
                                         # kontinuerleg trigging på sinus/støy.
    mqtt_endring_terskel: float = 100.0  # MQTT verdi-endring som triggar (høg nok
                                         # til å unngå flaum på normalt varierande verdiar)
    pre_trigger_ms: int = 1000           # Rå data FØR hending
    post_trigger_ms: int = 2000          # Rå data ETTER hending

    # MQTT-logging
    mqtt_logg_aktivert: bool = True


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
                sample_rate=int(data.get("sample_rate", 20000)),
                ssd_sti=str(data.get("ssd_sti", "")),
                ram_buffer_sekund=int(data.get("ram_buffer_sekund", 30)),
                hendingar_aktivert=bool(data.get("hendingar_aktivert", True)),
                rms_terskel_prosent=float(data.get("rms_terskel_prosent", 150.0)),
                dvdt_terskel=float(data.get("dvdt_terskel", 500.0)),
                mqtt_endring_terskel=float(data.get("mqtt_endring_terskel", 100.0)),
                pre_trigger_ms=int(data.get("pre_trigger_ms", 1000)),
                post_trigger_ms=int(data.get("post_trigger_ms", 2000)),
                mqtt_logg_aktivert=bool(data.get("mqtt_logg_aktivert", True)),
            )
            log.info(f"Lasta buffer-konfig: aktivert={konfig.aktivert}, "
                     f"intervall={konfig.intervall_ms}ms, "
                     f"maks={konfig.maks_storleik_mb}MB, "
                     f"sample_rate={konfig.sample_rate}")
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

    try:
        sample_rate = int(data.get("sample_rate", 20000))
    except (TypeError, ValueError):
        return None, "sample_rate maa vere eit heiltal"
    if sample_rate < 1000 or sample_rate > 200000:
        return None, f"sample_rate {sample_rate} utanfor gyldig omraade (1000-200000)"

    try:
        ram_buffer_sekund = int(data.get("ram_buffer_sekund", 30))
    except (TypeError, ValueError):
        return None, "ram_buffer_sekund maa vere eit heiltal"
    if ram_buffer_sekund < 5 or ram_buffer_sekund > 120:
        return None, f"ram_buffer_sekund {ram_buffer_sekund} utanfor gyldig omraade (5-120)"

    try:
        rms_terskel = float(data.get("rms_terskel_prosent", 150.0))
    except (TypeError, ValueError):
        return None, "rms_terskel_prosent maa vere eit tal"
    if rms_terskel < 101.0 or rms_terskel > 1000.0:
        return None, f"rms_terskel_prosent {rms_terskel} utanfor gyldig omraade (101-1000)"

    try:
        dvdt_terskel = float(data.get("dvdt_terskel", 500.0))
    except (TypeError, ValueError):
        return None, "dvdt_terskel maa vere eit tal"
    # Øvre grense høg nok for transient-/overspenningsdeteksjon: normal
    # nett-dV/dt toppar rundt ~100 V/ms (230 V/50 Hz), så terskelen må kunne
    # setjast godt over det for å unngå kontinuerleg trigging på rein sinus.
    if dvdt_terskel < 0.001 or dvdt_terskel > 1_000_000.0:
        return None, f"dvdt_terskel {dvdt_terskel} utanfor gyldig omraade (0.001-1000000)"

    try:
        mqtt_endring = float(data.get("mqtt_endring_terskel", 100.0))
    except (TypeError, ValueError):
        return None, "mqtt_endring_terskel maa vere eit tal"

    try:
        pre_trigger = int(data.get("pre_trigger_ms", 1000))
    except (TypeError, ValueError):
        return None, "pre_trigger_ms maa vere eit heiltal"
    if pre_trigger < 100 or pre_trigger > 30000:
        return None, f"pre_trigger_ms {pre_trigger} utanfor gyldig omraade (100-30000)"

    try:
        post_trigger = int(data.get("post_trigger_ms", 2000))
    except (TypeError, ValueError):
        return None, "post_trigger_ms maa vere eit heiltal"
    if post_trigger < 100 or post_trigger > 30000:
        return None, f"post_trigger_ms {post_trigger} utanfor gyldig omraade (100-30000)"

    return BufferKonfig(
        aktivert=bool(data.get("aktivert", True)),
        intervall_ms=intervall_ms,
        maks_storleik_mb=maks_mb,
        bevar_usynkronisert=bool(data.get("bevar_usynkronisert", True)),
        hub_sync_intervall_sek=sync_intervall,
        hub_batch_storleik=batch,
        hub_retensjon_dagar=retensjon,
        sample_rate=sample_rate,
        ssd_sti=str(data.get("ssd_sti", "")),
        ram_buffer_sekund=ram_buffer_sekund,
        hendingar_aktivert=bool(data.get("hendingar_aktivert", True)),
        rms_terskel_prosent=rms_terskel,
        dvdt_terskel=dvdt_terskel,
        mqtt_endring_terskel=mqtt_endring,
        pre_trigger_ms=pre_trigger,
        post_trigger_ms=post_trigger,
        mqtt_logg_aktivert=bool(data.get("mqtt_logg_aktivert", True)),
    ), None
