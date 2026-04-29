#!/usr/bin/env python3
"""
openDAQ Hub Server (Aggregator)
================================
Koplar til fleire fjern-nodar (Pi med SIRIUS) via openDAQ-protokollen
og eksponerer alle kanalar via eigne OPC-UA + NativeStreaming serverar.

DewesoftX på kontoret koplar til hubben og ser alle kanalar frå alle Pi-nodar.

Bruk:
  OPENDAQ_MODUS=hub python3 -m hub_server
"""

import sys
# Fiks dual-modul-problem: `python3 -m hub_server` lastar modulen som
# `__main__`, men `web_ui.py` importerer `from hub_server import ...`
# som skapar ein ANDRE modul med separate globale variablar.
if __name__ == '__main__':
    sys.modules.setdefault('hub_server', sys.modules[__name__])

import os
import time
import logging
import threading
from datetime import datetime

from hub_konfig import (
    HubKonfig, FjernNode,
    les_hub_konfig, lagre_hub_konfig, valider_hub_konfig,
    KanalRangeOverstyring, les_kanal_ranges, lagre_kanal_ranges, hent_range_map,
    ModbusRegister, NODE_TYPE_OPENDAQ, NODE_TYPE_MODBUS_TCP,
)
from buffer_konfig import les_buffer_konfig
from hub_buffer import HubBuffer
from modbus_manager import ModbusManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('hub_server')


# --- Logg-ringbuffer for web API ---

class LoggRingBuffer(logging.Handler):
    def __init__(self, kapasitet=500):
        super().__init__()
        self._linjer = []
        self._kapasitet = kapasitet
        self._lock = threading.Lock()

    def emit(self, record):
        linje = self.format(record)
        with self._lock:
            self._linjer.append(linje)
            if len(self._linjer) > self._kapasitet:
                self._linjer = self._linjer[-self._kapasitet:]

    def hent_linjer(self, antall=200):
        with self._lock:
            return list(self._linjer[-antall:])


_logg_buffer = LoggRingBuffer(kapasitet=2000)
_logg_buffer.setFormatter(logging.Formatter(
    '%(asctime)s [%(name)s/%(levelname)s] %(message)s', datefmt='%H:%M:%S'
))
logging.getLogger().addHandler(_logg_buffer)


# --- Globale variablar (trådtrygt med lock) ---

_hub_lock = threading.Lock()
_instance = None                # openDAQ Instance — BERRE server (DewesoftX koplar hit)
_klient_instance = None         # openDAQ Instance — BERRE klient (les fjern-nodar)
_hub_konfig: HubKonfig = HubKonfig()
_node_devices = {}              # node_id -> openDAQ device-objekt (på _klient_instance)
_node_status = {}               # node_id -> {"tilkobla": bool, "feil": str, ...}
_helsesjekk_aktiv = True
_hub_startet = None             # ISO timestamp
_hub_buffer: HubBuffer = None   # Hub-side buffer sync

# DataPacket-injeksjon (same mønster som opendaq_bro)
_kanal_signals = []      # [(ch, ISignalConfig)] per hub-kanal for send_packet
_dom_signals = []        # [ISignalConfig] domain signals
_dom_descs = []          # [DataDescriptor] domain descriptors
_val_descs = []          # [DataDescriptor] value descriptors
_total_samples = []      # [int] samples sendt per kanal
_pakett_klar = False     # True når DataPacket-injeksjon er klar
_tick_delta = 50000      # Ticks per sample (oppdaterast frå domain descriptor)
_ACQLOOP_TOGGLE = "/tmp/opendaq_disable_acq"
_fjern_kanal_info = []   # Info om fjern-kanalar for descriptor-bygging
_kanal_range_overstyringer = {}  # "node_id:kanal_namn" -> (low, high)

# --- Modbus TCP-nodar (via ModbusManager) ---
def _modbus_status_cb(node_id: str, status: dict):
    """Propager modbus-status til _node_status så hent_hub_status ser den."""
    with _hub_lock:
        eksisterande = _node_status.get(node_id, {})
        eksisterande.update(status)
        _node_status[node_id] = eksisterande


_modbus_manager = ModbusManager(status_callback=_modbus_status_cb)


def hent_logg(antall=200):
    """Returner dei siste N logg-linjene."""
    return _logg_buffer.hent_linjer(min(antall, 500))


# --- openDAQ Instance og tilkoblingar ---

def _opprett_instance():
    """Opprett TO openDAQ Instance-ar:

    1. _instance (server): Root device (daqref://device0) med DeviceInfo.
       NativeStreaming + OPC-UA serverar køyrer her. DewesoftX koplar hit.
       INGEN sub-devices — unngår 'DataType incompatible' OPC-UA feil
       som gjer at DewesoftX viser 'Disconnected'.

    2. _klient_instance (klient): Koplar til fjern-nodar via add_device().
       Ingen serverar — berre for å lese kanal-data til web UI og hub-buffer.
    """
    # Fjern acqLoop toggle-fil frå evt. tidlegare køyring
    try:
        os.remove(_ACQLOOP_TOGGLE)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    global _instance, _klient_instance

    # CWD må vere /usr/local/lib for at ModuleManager skal finne .module.so
    module_path = os.environ.get("OPENDAQ_MODULE_PATH", "/usr/local/lib")

    # IKKJE deaktiver RefDevice — DewesoftX krev at root device har kanalar.
    # Sub-device-kanalar har DataType incompatible-problem i OPC-UA, so
    # DewesoftX kan berre lese root device kanalar. Root device genererer
    # syntetiske signal som DewesoftX kan synkronisere mot.
    # os.environ.setdefault("OPENDAQ_DISABLE_ACQ", "1")  # DEAKTIVERT

    # Sett OPENDAQ_SERIAL om ikkje allereie sett — C++ patchen les denne
    # for DeviceInfo.serialNumber. Utan den vert det default "DevSer0".
    if not os.environ.get("OPENDAQ_SERIAL"):
        os.environ["OPENDAQ_SERIAL"] = "DB00000001"

    # Auto-detect MAC om ikkje allereie sett (backup for entrypoint)
    if not os.environ.get("OPENDAQ_MAC"):
        import glob as _glob
        for addr_file in _glob.glob("/sys/class/net/*/address"):
            try:
                mac = open(addr_file).read().strip()
                if mac and mac != "00:00:00:00:00:00":
                    os.environ["OPENDAQ_MAC"] = mac
                    log.info(f"  MAC auto-detect: {mac} ({addr_file})")
                    break
            except Exception:
                pass

    import opendaq as daq

    builder = daq.InstanceBuilder()
    builder.add_module_path(module_path)
    builder.add_discovery_server("mdns")
    builder.set_root_device("daqref://device0")
    _instance = builder.build()
    log.info("openDAQ Instance oppretta (hub-modus, root device + klient)")

    # Start med 1 kanal — vert oppdatert til totalt antal fjern-kanalar
    # etter at nodar er tilkobla (_oppdater_root_kanalar).
    # DewesoftX krev at root device har kanalar — sub-device kanalar
    # har DataType incompatible-problem og kan ikkje lesast.
    try:
        _instance.set_property_value("NumberOfChannels", 1)
        log.info("  Root device: NumberOfChannels sett til 1 (oppdaterast etter node-tilkobling)")
    except Exception as e:
        log.warning(f"  Kunne ikkje sette NumberOfChannels: {e}")

    # GlobalSampleRate — DewesoftX forventar denne eigenskapen
    sample_rate = float(os.environ.get("SAMPLE_RATE", "20000"))
    try:
        _instance.set_property_value("GlobalSampleRate", sample_rate)
        log.info(f"  Root device: GlobalSampleRate sett til {sample_rate} Hz")
    except Exception as e:
        log.warning(f"  Kunne ikkje sette GlobalSampleRate: {e}")

    # GetPossibleSampleRate — DewesoftX TDSOpenDaqAI.CalcADCSampleRate treng denne
    try:
        try:
            _instance.get_property("GetPossibleSampleRate")
        except Exception:
            prop = daq.FloatPropertyBuilder("GetPossibleSampleRate", 200000.0)
            _instance.add_property(prop.build())
            log.info("  Root device: GetPossibleSampleRate = 200000 Hz")
    except Exception as e:
        log.warning(f"  GetPossibleSampleRate feilet: {e}")

    # DeviceLogLevel + DeviceLogPath — DewesoftX settings panel krasjar utan desse
    for namn, typ, default in [
        ("DeviceLogLevel", "int", 2),
        ("DeviceLogPath", "string", ""),
    ]:
        try:
            try:
                _instance.get_property(namn)
            except Exception:
                if typ == "int":
                    prop = daq.IntPropertyBuilder(namn, int(default))
                    _instance.add_property(prop.build())
                elif typ == "string":
                    prop = daq.StringPropertyBuilder(namn, str(default))
                    _instance.add_property(prop.build())
                log.info(f"  DewesoftX-prop {namn} = {default!r}")
        except Exception as e:
            log.warning(f"  DewesoftX-prop {namn} feilet: {e}")

    # Logg DeviceInfo for verifisering
    try:
        info = _instance.info
        log.info(f"  DeviceInfo: serial={info.serial_number}, model={info.model}, manufacturer={info.manufacturer}")
    except Exception as e:
        log.debug(f"  Kunne ikkje lese DeviceInfo: {e}")

    # --- Klient-instans (koplar til fjern-nodar, ingen serverar) ---
    klient_builder = daq.InstanceBuilder()
    klient_builder.add_module_path(module_path)
    _klient_instance = klient_builder.build()
    log.info("Klient-instans oppretta (for fjern-node-tilkoblingar)")


_kanal_mapping = []     # [(node_id, ch_index, scale, offset), ...]
_relay_readers = {}     # (node_id, signal_id) -> StreamReader
_relay_aktiv = True


def _safe_set(obj, namn, verdi):
    """Set property med type-konvertering og clamping (same som opendaq_bro)."""
    import opendaq as daq
    try:
        prop = obj.get_property(namn)
        vtype = prop.value_type
        ct = getattr(daq, 'CoreType', None)
        if ct is not None:
            if vtype == ct.ctFloat:
                v = float(verdi)
                try:
                    v = max(v, float(prop.min_value))
                except Exception:
                    pass
                try:
                    v = min(v, float(prop.max_value))
                except Exception:
                    pass
                obj.set_property_value(namn, v)
                return True
            if vtype == ct.ctInt:
                v = int(round(verdi)) if isinstance(verdi, float) else int(verdi)
                try:
                    v = max(v, int(prop.min_value))
                except Exception:
                    pass
                try:
                    v = min(v, int(prop.max_value))
                except Exception:
                    pass
                obj.set_property_value(namn, v)
                return True
        obj.set_property_value(namn, verdi)
        return True
    except Exception as e:
        log.warning(f"  _safe_set({namn}, {verdi!r}): {e}")
        return False


def _oppdater_root_kanalar():
    """Oppdater root device kanalar til å matche fjern-nodar.

    Må kallast ETTER node-tilkobling og FØR server-start.

    1. Set NumberOfChannels til totalt antal fjern-kanalar
    2. Konfigurer kvar kanal med namn, range, amplitude frå remote
    3. Legg til GetPossibleSampleRate på kvar kanal (DewesoftX krev dette)
    4. Bygg _kanal_mapping for data-relay-tråden
    5. Lagre fjern-kanal info for descriptor-bygging (etter server-start)
    """
    global _instance, _kanal_mapping, _fjern_kanal_info

    import opendaq as daq

    # --- Hent fjern-kanal info (namn, range, eining) ---
    fjern_kanalar = []  # [{namn, range_low, range_high, eining, node_id, ch_idx, kanal_type, ...}]
    with _hub_lock:
        # Stabil rekkefølge: same som _hub_konfig.nodar
        for node in _hub_konfig.nodar:
            if node.type == NODE_TYPE_MODBUS_TCP:
                # Modbus: kvar register = ein kanal (uavhengig av tilkoblingstatus;
                # verdiar vert oppdatert av polling-tråden, None til fyrste lesing)
                for ri, reg in enumerate(node.modbus_registers):
                    fjern_kanalar.append({
                        "namn": reg.namn,
                        "range_low": reg.range_low,
                        "range_high": reg.range_high,
                        "eining": reg.eining,
                        "node_id": node.id,
                        "ch_idx": ri,
                        "kanal_type": "modbus",
                        "modbus_adresse": reg.adresse,
                    })
                    log.info(f"  Modbus register {ri}: '{reg.namn}' @{reg.adresse} "
                             f"[{reg.range_low}, {reg.range_high}] {reg.eining}")
                continue

            # openDAQ-nodar
            dev = _node_devices.get(node.id)
            if not dev:
                continue
            try:
                channels = dev.channels
                for ci in range(len(channels)):
                    ch = channels[ci]
                    namn = "ukjent"
                    range_low, range_high = -5.0, 5.0
                    eining = ""
                    sig_id = f"{node.id}_{ci}"   # Fallback-id
                    try:
                        namn = ch.name
                    except Exception:
                        pass
                    # Les CustomRange frå remote kanal
                    try:
                        cr = ch.get_property_value("CustomRange")
                        range_low = float(cr.low_value)
                        range_high = float(cr.high_value)
                    except Exception:
                        pass
                    # Les eining + range frå signal descriptor
                    try:
                        sig = ch.signals[0]
                        try:
                            sig_id = sig.global_id
                        except Exception:
                            pass
                        desc = sig.descriptor
                        if desc:
                            if desc.unit:
                                eining = desc.unit.symbol or ""
                            if range_low == -5.0 and range_high == 5.0:
                                # CustomRange feilet — prøv descriptor
                                if desc.value_range:
                                    range_low = float(desc.value_range.low_value)
                                    range_high = float(desc.value_range.high_value)
                    except Exception:
                        pass
                    fjern_kanalar.append({
                        "namn": namn,
                        "range_low": range_low,
                        "range_high": range_high,
                        "eining": eining,
                        "node_id": node.id,
                        "ch_idx": ci,
                        "kanal_type": "opendaq",
                        "sig_id": sig_id,
                    })
                    log.info(f"  Remote kanal {ci}: '{namn}' [{range_low}, {range_high}] {eining}")
            except Exception as e:
                log.warning(f"  Kunne ikkje lese kanalar frå '{node.namn}': {e}")

    n_kanalar = len(fjern_kanalar)
    if n_kanalar < 1:
        n_kanalar = 1
        fjern_kanalar = [{"namn": "AI 0", "range_low": -5.0, "range_high": 5.0,
                          "eining": "", "node_id": "", "ch_idx": 0}]
        log.info("  Ingen fjern-kanalar — brukar 1 dummy-kanal")
    else:
        log.info(f"  {n_kanalar} fjern-kanalar funne")

    # Lagre globalt for descriptor-bygging etter server-start
    _fjern_kanal_info = fjern_kanalar

    # --- Set NumberOfChannels ---
    try:
        _instance.set_property_value("NumberOfChannels", n_kanalar)
        log.info(f"  Root device: NumberOfChannels → {n_kanalar}")
    except Exception as e:
        log.warning(f"  Kunne ikkje sette NumberOfChannels={n_kanalar}: {e}")
        return

    # --- Konfigurer kvar kanal ---
    try:
        hub_channels = list(_instance.channels)
    except Exception:
        log.warning("  Kunne ikkje lese hub-kanalar")
        return

    _kanal_mapping = []

    for i, fk in enumerate(fjern_kanalar):
        if i >= len(hub_channels):
            break
        ch = hub_channels[i]

        # Namn
        try:
            ch.name = fk["namn"]
        except Exception:
            pass

        # Amplitude=10 aktiverer PostScaling frå CustomRange.
        # Waveform=2 (None) → acqLoop output = 0*Amp + DC = berre DC, ingen oscillasjon.
        # Waveform-enum: 0=Sine, 1=Rect, 2=None, 3=Counter
        _safe_set(ch, "Amplitude", 10.0)
        _safe_set(ch, "Waveform", 2)       # None — konstant null, berre DC tel
        _safe_set(ch, "Frequency", 0.1)    # Irrelevant med Waveform=None, men set for tryggleik
        _safe_set(ch, "DC", 0.0)

        # CustomRange — bruk override viss sett, elles utvid med sikkerheitsfaktor.
        r_low = fk["range_low"]     # Original range (for descriptor display)
        r_high = fk["range_high"]
        override_key = f"{fk['node_id']}:{fk['namn']}"
        override = _kanal_range_overstyringer.get(override_key)
        if override:
            cr_low, cr_high = override
            log.info(f"  Kanal '{fk['namn']}': brukar override range [{cr_low}, {cr_high}]")
        else:
            # Intern DC er avgrensa til [-10, 10]. Med remote range [-1000, 1000]
            # og scale=100, vert 2688W → intern 26.88 → clampa til 10 → viser 1000W.
            # Med 5x margin: [-5000, 5000], scale=500, 2688W → intern 5.376 → OK.
            RANGE_FAKTOR = 5.0
            max_abs = max(abs(r_low), abs(r_high), 1.0)
            cr_half = max_abs * RANGE_FAKTOR
            cr_low = -cr_half           # Utvida range (for CustomRange + PostScaling)
            cr_high = cr_half
        try:
            custom_range = daq.Range(cr_low, cr_high)
            ch.set_property_value("CustomRange", custom_range)
        except Exception as e:
            log.warning(f"  CustomRange({cr_low}, {cr_high}) for '{fk['namn']}': {e}")

        # Lagre utvida range i fjern_kanal_info for PostScaling-bygging
        fk["cr_low"] = cr_low
        fk["cr_high"] = cr_high

        # Skaleringsdata for DC relay:
        # CustomRange mappar intern [-10,10] til fysisk [cr_low, cr_high]
        # Intern DC = (fysisk_verdi - offset) / scale
        span = cr_high - cr_low
        scale = span / 20.0 if span > 0 else 1.0   # 20 = intern span [-10,10]
        offset = (cr_high + cr_low) / 2.0
        kanal_type = fk.get("kanal_type", "opendaq")
        _kanal_mapping.append((fk["node_id"], fk["ch_idx"], scale, offset, kanal_type))

        # GetPossibleSampleRate — DewesoftX krev dette på KVAR kanal
        try:
            try:
                ch.get_property("GetPossibleSampleRate")
            except Exception:
                prop = daq.FloatPropertyBuilder("GetPossibleSampleRate", 200000.0)
                ch.add_property(prop.build())
        except Exception:
            pass

        # Signal descriptors vert sett ETTER server-start via _init_data_injeksjon()
        # (ISignalConfig.cast_from krev at signal er fullt initialisert)

        # Verifiser Waveform-verdi (berre fyrste kanal)
        wf_actual = "?"
        try:
            wf_actual = ch.get_property_value("Waveform")
        except Exception:
            pass

        log.info(f"  Kanal {i}: '{fk['namn']}' range=[{r_low},{r_high}] CR=[{cr_low},{cr_high}] "
                 f"{fk.get('eining', '')} scale={scale:.1f} offset={offset:.1f} wf={wf_actual}")

    log.info(f"  {len(_kanal_mapping)} hub-kanalar konfigurert")


def injiser_push_verdiar(node_id_eller_namn: str, kanalar: dict) -> int:
    """Injiser verdiar frå push-batch i lokale hub-kanalar (DC-relay).

    Brukast av /api/ingest når ein node POST-ar batch. DewesoftX ser
    desse verdiane via openDAQ-bridge (acqLoop sin DC-property).

    Args:
        node_id_eller_namn: matcher mot FjernNode.id eller FjernNode.namn
        kanalar: dict {kanal_namn: fysisk_verdi}

    Returns:
        Antal kanalar som vart oppdatert.
    """
    if not kanalar or not _instance:
        return 0

    # Finn hub-node med matching id eller namn (fuzzy via lower-case)
    target_node = None
    nid_norm = node_id_eller_namn.lower().strip()
    try:
        for n in _hub_konfig.nodar:
            if n.id == node_id_eller_namn or n.namn == node_id_eller_namn:
                target_node = n
                break
            if n.namn.lower().strip() == nid_norm:
                target_node = n
                break
    except Exception:
        return 0
    if target_node is None:
        return 0

    # Hent hub-channel-liste éin gong
    try:
        hub_channels = list(_instance.channels)
    except Exception:
        return 0

    oppdatert = 0
    # For kvar kanal i push: finn matching hub-kanal med same node_id + namn
    for ch_namn, verdi in kanalar.items():
        if not isinstance(verdi, (int, float)):
            continue
        for hub_idx, fk in enumerate(_fjern_kanal_info):
            if fk.get("kanal_type") != "opendaq":
                continue
            if fk.get("node_id") != target_node.id:
                continue
            if fk.get("namn") != ch_namn:
                continue
            if hub_idx >= len(hub_channels):
                continue
            # Hent scale/offset frå _kanal_mapping for fysisk → intern DC
            try:
                _, _, scale, offset, _ = _kanal_mapping[hub_idx]
            except Exception:
                scale, offset = 1.0, 0.0
            if scale == 0:
                scale = 1.0
            dc_val = (float(verdi) - offset) / scale
            try:
                _safe_set(hub_channels[hub_idx], "DC", dc_val)
                oppdatert += 1
            except Exception:
                pass
            break  # gå til neste kanal i push-batch
    return oppdatert


def _init_data_injeksjon():
    """Initialiser DataPacket-injeksjon etter server-start.

    Same mønster som opendaq_bro:
    1. Cast signal → ISignalConfig for send_packet()
    2. Hent domain signal + descriptor
    3. Bygg signal descriptors med einingar frå remote
    4. Deaktiver acqLoop (toggle-fil)
    5. Send DescriptorChanged events

    Viss dette feiler, brukar relay-tråden DC-fallback i staden.
    """
    global _kanal_signals, _dom_signals, _dom_descs, _val_descs
    global _total_samples, _pakett_klar, _tick_delta

    import opendaq as daq

    log.info("Initialiserer DataPacket-injeksjon...")

    # Sjekk API-tilgjengelegheit
    for attr in ('DataPacket', 'DataPacketWithDomain', 'ISignalConfig',
                 'DataDescriptorBuilder', 'UnitBuilder', 'SampleType'):
        if not hasattr(daq, attr):
            log.warning(f"  DataPacket init: {attr} ikkje tilgjengeleg — brukar DC fallback")
            return

    # Hent kanalar frå server-instansen
    try:
        channels = list(_instance.channels)
    except Exception:
        log.warning("  DataPacket init: kunne ikkje lese hub-kanalar")
        return

    _kanal_signals = []
    _dom_signals = []
    _dom_descs = []
    _val_descs = []
    _total_samples = []

    kanalar_klar = 0
    for idx, ch in enumerate(channels):
        try:
            sigs = list(ch.signals)
            if not sigs:
                raise ValueError("Ingen signal på kanal")

            raw_sig = sigs[0]

            # Cast til ISignalConfig for send_packet() — same som opendaq_bro
            sig_config = daq.ISignalConfig.cast_from(raw_sig)
            _kanal_signals.append((ch, sig_config))

            # Hent domain signal (read-only fyrst, deretter cast)
            dom_sig_raw = raw_sig.domain_signal
            if dom_sig_raw is None:
                raise ValueError("domain_signal er None")
            dom_sig = daq.ISignalConfig.cast_from(dom_sig_raw)
            _dom_signals.append(dom_sig)

            # Hent descriptors
            dom_desc = dom_sig_raw.descriptor
            val_desc = raw_sig.descriptor
            _dom_descs.append(dom_desc)
            _val_descs.append(val_desc)
            _total_samples.append(0)

            # Diagnostikk: sjekk PostScaling i eksisterande descriptor
            try:
                ps = val_desc.post_scaling if val_desc else None
                has_ps = ps is not None
                ch_name = ch.name if hasattr(ch, 'name') else f"ch{idx}"
                log.info(f"  Kanal '{ch_name}': PostScaling={'JA' if has_ps else 'NEI'}")
                if has_ps:
                    # Logg PostScaling-detaljar (fyrste kanal)
                    if idx == 0:
                        ps_attrs = [a for a in dir(ps) if not a.startswith('_')]
                        log.info(f"    PostScaling attrs: {ps_attrs[:15]}")
            except Exception:
                pass

            # Les tick_delta frå domain descriptor (ticks per sample)
            try:
                rule = dom_desc.rule
                params = rule.parameters
                if hasattr(params, 'get'):
                    delta = params.get('delta', _tick_delta)
                elif hasattr(params, '__getitem__'):
                    delta = params['delta']
                else:
                    delta = _tick_delta
                _tick_delta = int(delta)
            except Exception:
                pass

            kanalar_klar += 1

        except Exception as e:
            ch_name = ch.name if hasattr(ch, 'name') else f"ch{idx}"
            log.warning(f"  DataPacket init: Kanal '{ch_name}' feilet: {e}")
            _kanal_signals.append((ch, None))
            _dom_signals.append(None)
            _dom_descs.append(None)
            _val_descs.append(None)
            _total_samples.append(0)

    if kanalar_klar == 0:
        log.warning("  DataPacket init: Ingen kanalar klare — brukar DC relay fallback")
        return

    log.info(f"  DataPacket init: {kanalar_klar}/{len(channels)} kanalar klare, "
             f"tick_delta={_tick_delta}")

    # Sett signal descriptors med einingar og range frå fjern-nodar
    n_desc = _sett_hub_descriptors()

    # IKKJE deaktiver acqLoop — la den køyre for kontinuerleg 20 kHz data.
    # DC relay oppdaterer DC-eigenskapen, acqLoop genererer signal.
    # Utan acqLoop får DewesoftX "Invalid or no data" fordi DataPacket-relay
    # ikkje leverer data raskt nok (polling vs callback).
    log.info(f"  acqLoop AKTIV (DC relay modus, descriptors sett: {n_desc})")

    # Send DescriptorChanged events viss descriptors vart sett
    if n_desc > 0:
        _send_descriptor_events()

    # IKKJE sett _pakett_klar — bruk DC relay i staden for DataPacket-injeksjon.
    # DataPacket-injeksjon krev nøyaktig timing som polling-basert relay
    # ikkje kan levere påliteleg.
    log.info(f"  Brukar DC relay (acqLoop genererer data, DC styrer verdiar)")


def _bygg_post_scaling(scale_factor, offset_val):
    """Bygg LinearScaling (PostScaling) for intern→fysisk konvertering.

    physical = internal * scale_factor + offset_val

    Prøver fleire openDAQ API-variantar sidan Python-bindings varierer.
    Returnerer (scaling_obj, metode_namn) eller (None, feilmeldingar).
    """
    import opendaq as daq

    forsøk = []

    # 1. LinearScaling med ScaledSampleType
    if hasattr(daq, 'ScaledSampleType'):
        try:
            ps = daq.LinearScaling(scale_factor, offset_val,
                                   daq.SampleType.Float64,
                                   daq.ScaledSampleType.Float64)
            return ps, "LinearScaling(s,o,ST,SST)"
        except Exception as e:
            forsøk.append(f"LinearScaling(s,o,ST,SST): {e}")

    # 2. LinearScaling med SampleType for begge
    try:
        ps = daq.LinearScaling(scale_factor, offset_val,
                               daq.SampleType.Float64,
                               daq.SampleType.Float64)
        return ps, "LinearScaling(s,o,ST,ST)"
    except Exception as e:
        forsøk.append(f"LinearScaling(s,o,ST,ST): {e}")

    # 3. LinearScaling berre med scale og offset
    try:
        ps = daq.LinearScaling(scale_factor, offset_val)
        return ps, "LinearScaling(s,o)"
    except Exception as e:
        forsøk.append(f"LinearScaling(s,o): {e}")

    return None, forsøk


def _sett_hub_descriptors():
    """Sett signal descriptors med einingar, range og PostScaling.

    PostScaling (LinearScaling) mappar interne verdiar [-10,10] til
    fysiske verdiar basert på CustomRange. Utan PostScaling viser
    DewesoftX rå interne DC-verdiar.

    Strategi:
    1. Prøv å kopiere PostScaling frå eksisterande descriptor
    2. Viss ikkje: bygg LinearScaling manuelt
    3. Viss begge feiler: IKKJE overskriv descriptor (behald RefDevice sin med PostScaling)

    Returnerer antal vellukka descriptor-settingar.
    """
    import opendaq as daq

    sett = 0

    # Diagnostikk: logg tilgjengelege scaling-API
    scaling_attrs = [a for a in dir(daq) if 'scal' in a.lower() or 'linear' in a.lower()]
    log.info(f"  openDAQ scaling API: {scaling_attrs}")

    for idx in range(len(_kanal_signals)):
        if idx >= len(_fjern_kanal_info):
            break
        _, sig = _kanal_signals[idx]
        if sig is None:
            continue

        fk = _fjern_kanal_info[idx]
        try:
            # Bygg eining (Unit)
            eining = fk.get("eining", "")
            unit_builder = daq.UnitBuilder()
            unit_builder.symbol = eining or ""
            if eining == "V":
                unit_builder.name = "volt"
                unit_builder.quantity = "voltage"
            elif eining == "A":
                unit_builder.name = "ampere"
                unit_builder.quantity = "electric_current"
            elif eining == "W":
                unit_builder.name = "watt"
                unit_builder.quantity = "power"
            elif eining in ("°C", "C"):
                unit_builder.name = "degree Celsius"
                unit_builder.quantity = "temperature"
            else:
                unit_builder.name = eining or "unknown"
                unit_builder.quantity = ""
            unit_obj = unit_builder.build()

            # PostScaling: physical = internal * scale + offset
            # Brukar UTVIDA range (cr_low/cr_high) for PostScaling — same som CustomRange.
            # Original range (range_low/range_high) brukast for descriptor display.
            r_low = fk["range_low"]       # original (for display)
            r_high = fk["range_high"]
            cr_low = fk.get("cr_low", r_low)   # utvida (for PostScaling)
            cr_high = fk.get("cr_high", r_high)
            ps_scale = (cr_high - cr_low) / 20.0   # 20 = intern span [-10,+10]
            ps_offset = (cr_high + cr_low) / 2.0

            existing_desc = _val_descs[idx] if idx < len(_val_descs) else None

            # --- Hent PostScaling ---
            post_scaling = None
            ps_metode = "ingen"

            # Prøv 1: Kopier frå eksisterande descriptor
            if existing_desc is not None:
                try:
                    ps = existing_desc.post_scaling
                    if ps is not None:
                        post_scaling = ps
                        ps_metode = "kopiert"
                except Exception:
                    pass

            # Prøv 2: Bygg manuelt med LinearScaling
            if post_scaling is None:
                ps, info = _bygg_post_scaling(ps_scale, ps_offset)
                if ps is not None:
                    post_scaling = ps
                    ps_metode = f"manuell ({info})"
                elif idx == 0:
                    log.warning(f"  LinearScaling-forsøk feilet: {info}")

            # Viss vi ikkje har PostScaling: IKKJE overskriv descriptor.
            # RefDevice sin originale descriptor har PostScaling frå CustomRange.
            # Betre med rett skalering og feil eining enn feil skalering.
            if post_scaling is None:
                if idx == 0:
                    log.warning(f"  Kan ikkje lage PostScaling — beheld original descriptor "
                                f"(DewesoftX får rett skalering men feil eining)")
                continue

            # Bygg ny descriptor med unit + PostScaling
            desc_builder = daq.DataDescriptorBuilder()
            desc_builder.name = fk["namn"]
            desc_builder.sample_type = daq.SampleType.Float64
            desc_builder.unit = unit_obj
            try:
                desc_builder.value_range = daq.Range(r_low, r_high)
            except Exception:
                pass

            # Set PostScaling
            try:
                desc_builder.post_scaling = post_scaling
            except Exception as e_ps:
                # Prøv alternativ metode
                setter = getattr(desc_builder, 'set_post_scaling', None)
                if setter:
                    setter(post_scaling)
                else:
                    log.warning(f"  desc_builder.post_scaling feilet: {e_ps}")
                    continue

            # Kopier rule frå eksisterande descriptor (sample rate info)
            if existing_desc is not None:
                try:
                    rule = existing_desc.rule
                    if rule is not None:
                        desc_builder.rule = rule
                except Exception:
                    pass

            new_desc = desc_builder.build()

            # Sett descriptor på signalet
            descriptor_sett = False
            try:
                sig.descriptor = new_desc
                descriptor_sett = True
            except Exception:
                for method_name in ('set_descriptor', 'setDescriptor'):
                    fn = getattr(sig, method_name, None)
                    if fn:
                        try:
                            fn(new_desc)
                            descriptor_sett = True
                            break
                        except Exception:
                            pass

            if descriptor_sett:
                if idx < len(_val_descs):
                    _val_descs[idx] = new_desc
                sett += 1
                log.info(f"  Descriptor: '{fk['namn']}' unit={eining} "
                         f"display=[{r_low},{r_high}] CR=[{cr_low},{cr_high}] "
                         f"postScaling={ps_metode} "
                         f"(scale={ps_scale:.1f}, offset={ps_offset:.1f})")

        except Exception as e:
            log.warning(f"  Descriptor '{fk.get('namn', idx)}' feilet: {e}")

    log.info(f"  Signal descriptors sett: {sett}/{len(_kanal_signals)}")
    return sett


def _send_descriptor_events():
    """Send DataDescriptorChangedEventPacket til alle signalar.

    NativeStreaming-serveren treng dette for å vite dataformatet
    FØR den byrjar å vidaresende DataPackets til klientar.
    Same som opendaq_bro.send_descriptor_events().
    """
    import opendaq as daq

    if not hasattr(daq, 'DataDescriptorChangedEventPacket'):
        log.warning("  DataDescriptorChangedEventPacket ikkje tilgjengeleg")
        return

    evt_sendt = 0
    for idx in range(len(_kanal_signals)):
        if (idx < len(_dom_signals) and idx < len(_val_descs) and
                idx < len(_dom_descs)):
            dom_sig = _dom_signals[idx]
            val_desc = _val_descs[idx]
            dom_desc = _dom_descs[idx]
            _, sig = _kanal_signals[idx]

            if sig is None or dom_sig is None or val_desc is None:
                continue

            try:
                evt = daq.DataDescriptorChangedEventPacket(val_desc, dom_desc)
                sig.send_packet(evt)
                evt_sendt += 1
            except Exception as e:
                log.warning(f"  DescriptorChanged event Ch{idx}: {e}")

    if evt_sendt > 0:
        log.info(f"  DescriptorChanged events sendt: {evt_sendt}")


def _les_fjern_verdiar():
    """Les siste verdi frå alle fjern-kanalar via StreamReader.

    Returnerer liste med fysiske verdiar (None viss ikkje tilgjengeleg).
    """
    import opendaq as daq

    verdiar = [None] * len(_kanal_mapping)

    with _hub_lock:
        devices = dict(_node_devices)

    for idx, entry in enumerate(_kanal_mapping):
        nid, remote_idx, scale, offset, kanal_type = entry
        if not nid:
            continue

        if kanal_type == "modbus":
            # Modbus: slå opp cached verdi frå manager
            try:
                reg_adr = _fjern_kanal_info[idx].get("modbus_adresse")
            except (IndexError, AttributeError):
                reg_adr = None
            if reg_adr is not None:
                mb_verdiar = _modbus_manager.hent_verdiar()
                v = mb_verdiar.get((nid, reg_adr))
                if v is not None:
                    verdiar[idx] = v
            continue

        # openDAQ: les via StreamReader
        dev = devices.get(nid)
        if not dev:
            continue

        try:
            remote_ch = dev.channels[remote_idx]
            signals = remote_ch.signals
            if not signals or len(signals) == 0:
                continue
            sig = signals[0]

            try:
                sig_id = sig.global_id
            except Exception:
                sig_id = f"{nid}_{remote_idx}"

            key = (nid, sig_id)
            if key not in _relay_readers:
                try:
                    _relay_readers[key] = daq.StreamReader(sig)
                except Exception:
                    continue

            reader = _relay_readers[key]
            count = reader.available_count
            if count > 50000:
                # Streaming overload (typisk treig Tailscale DERP) — drop
                # reader så han vert lazy-oppretta neste runde.
                _relay_readers.pop(key, None)
                continue
            if count > 0:
                chunk = min(count, 1000)
                values = reader.read(chunk)
                if values is not None and len(values) > 0:
                    verdiar[idx] = float(values[-1])
                if count > chunk:
                    try:
                        reader.skip(count - chunk)
                    except Exception:
                        pass
        except Exception:
            pass

    return verdiar


def _data_relay_loop():
    """Bakgrunnstråd: send data frå fjern-nodar til hub-kanalar.

    Primær: DataPacket-injeksjon (interne verdiar, PostScaling handterer konvertering).
    Fallback: DC-relay (set DC-eigenskapen direkte).

    Sender INTERNE verdiar (ikkje fysiske) sidan RefDevice-descriptors
    har PostScaling frå CustomRange+Amplitude=10 som mappar [-10,10] → fysisk range.
    Konvertering: internal = (physical - offset) / scale
    """
    global _relay_aktiv, _pakett_klar

    import opendaq as daq
    import ctypes
    import traceback

    log.info("Data-relay tråd starta")
    time.sleep(3)  # Vent til serverar + DataPacket-init er ferdig

    BLOKK = 1024  # samples per pakke (same som opendaq_bro)
    sample_rate = float(os.environ.get("SAMPLE_RATE", "20000"))
    intervall = BLOKK / sample_rate  # ~51.2ms ved 20kHz
    relay_teller = 0

    try:
        while _relay_aktiv:
            if not _pakett_klar:
                # --- FALLBACK: DC relay (set DC-eigenskapen) ---
                _dc_relay_steg()
                time.sleep(0.5)
                continue

            time.sleep(intervall)

            try:
                # Les siste fysiske verdiar frå fjern-nodar
                verdiar = _les_fjern_verdiar()
            except Exception as e:
                if relay_teller % 200 == 0:
                    log.warning(f"  Relay: _les_fjern_verdiar feilet: {e}")
                relay_teller += 1
                continue

            # Send DataPackets
            for i, verdi in enumerate(verdiar):
                if i >= len(_kanal_signals) or i >= len(_dom_signals):
                    break
                _, sig = _kanal_signals[i]
                dom_sig = _dom_signals[i]
                val_desc = _val_descs[i]
                dom_desc = _dom_descs[i]

                if sig is None or dom_sig is None or val_desc is None:
                    continue

                tick_offset = _total_samples[i] * _tick_delta

                try:
                    time_pkt = daq.DataPacket(dom_desc, BLOKK, tick_offset)
                    val_pkt = daq.DataPacketWithDomain(
                        time_pkt, val_desc, BLOKK, 0)

                    # Konverter fysisk → intern verdi for PostScaling
                    # PostScaling frå CustomRange+Amplitude=10 mappar [-10,10] → fysisk
                    v = 0.0
                    if verdi is not None and i < len(_kanal_mapping):
                        _, _, scale, offset, _ = _kanal_mapping[i]
                        if scale != 0:
                            v = (verdi - offset) / scale
                        else:
                            v = verdi
                        # Klamp til intern range [-10, 10]
                        v = max(-10.0, min(10.0, v))

                    arr = (ctypes.c_double * BLOKK).from_address(
                        int(val_pkt.raw_data))
                    for j in range(BLOKK):
                        arr[j] = v

                    dom_sig.send_packet(time_pkt)
                    sig.send_packet(val_pkt)
                    _total_samples[i] += BLOKK
                except Exception as e:
                    if relay_teller % 200 == 0:
                        log.warning(f"  DataPacket relay Ch{i}: {e}")

            relay_teller += 1
            if relay_teller == 1 or relay_teller % 1000 == 0:
                v_str = [f"{v:.1f}" if v is not None else "?" for v in verdiar[:3]]
                log.info(f"  Relay pkt #{relay_teller}: verdiar={v_str}...")

    except Exception as e:
        log.error(f"  Data-relay tråd KRASJA: {e}")
        log.error(traceback.format_exc())
        # Re-enable acqLoop so DewesoftX gets SOME data
        _pakett_klar = False
        try:
            os.remove(_ACQLOOP_TOGGLE)
            log.info("  acqLoop RE-AKTIVERT (fallback etter relay-krasj)")
        except Exception:
            pass


def _dc_relay_steg():
    """Fallback: set DC-eigenskapen på hub-kanalar (krev PostScaling frå CustomRange)."""
    import opendaq as daq

    try:
        hub_channels = list(_instance.channels)
    except Exception:
        return

    with _hub_lock:
        devices = dict(_node_devices)

    for hub_idx, entry in enumerate(_kanal_mapping):
        nid, remote_idx, scale, offset, kanal_type = entry
        if hub_idx >= len(hub_channels) or not nid:
            continue

        # Modbus-kanalar: hent cached verdi frå manager
        if kanal_type == "modbus":
            try:
                reg_adr = _fjern_kanal_info[hub_idx].get("modbus_adresse")
            except (IndexError, AttributeError):
                continue
            if reg_adr is None:
                continue
            physical_val = _modbus_manager.hent_verdiar().get((nid, reg_adr))
            if physical_val is None:
                continue
            if scale != 0:
                dc_val = (physical_val - offset) / scale
            else:
                dc_val = physical_val
            _safe_set(hub_channels[hub_idx], "DC", dc_val)
            continue

        # openDAQ-kanalar: les via StreamReader
        dev = devices.get(nid)
        if not dev:
            continue

        try:
            remote_ch = dev.channels[remote_idx]
            signals = remote_ch.signals
            if not signals or len(signals) == 0:
                continue
            sig = signals[0]

            try:
                sig_id = sig.global_id
            except Exception:
                sig_id = f"{nid}_{remote_idx}"

            key = (nid, sig_id)
            if key not in _relay_readers:
                try:
                    _relay_readers[key] = daq.StreamReader(sig)
                except Exception:
                    continue

            reader = _relay_readers[key]
            count = reader.available_count
            if count > 50000:
                _relay_readers.pop(key, None)
                continue
            if count > 0:
                chunk = min(count, 1000)
                values = reader.read(chunk)
                if values is not None and len(values) > 0:
                    physical_val = float(values[-1])
                    # Skaler til intern DC-range: DC = (physical - offset) / scale
                    if scale != 0:
                        dc_val = (physical_val - offset) / scale
                    else:
                        dc_val = physical_val
                    _safe_set(hub_channels[hub_idx], "DC", dc_val)
                if count > chunk:
                    try:
                        reader.skip(count - chunk)
                    except Exception:
                        pass
        except Exception:
            pass


def _koble_til_node(node: FjernNode) -> bool:
    """Prøv å koble til ein fjern-node.

    openDAQ: add_device() via OPC-UA (konfig) + auto NativeStreaming (data).
    Modbus TCP: start polling-tråd som les register.
    """
    if node.type == NODE_TYPE_MODBUS_TCP:
        return _start_modbus_node(node)

    global _klient_instance, _node_devices, _node_status

    import opendaq as daq

    tilkobling = node.tilkobling_streng
    log.info(f"Koplar til node '{node.namn}' ({tilkobling})...")

    # Diagnostikk: list tilgjengelege device-typar (berre fyrste gong)
    try:
        dev_types = _klient_instance.available_device_types
        log.info(f"  Tilgjengelege device-typar: {list(dev_types.keys())}")
    except Exception:
        pass

    def _prøv_tilkobling(conn_str):
        """Prøv add_device på klient-instansen."""
        config = None
        try:
            dev_types = _klient_instance.available_device_types
            for type_id in dev_types:
                tid = type_id.lower()
                if 'opcua' in conn_str and ('opcua' in tid):
                    config = dev_types[type_id].create_default_config()
                    for p in config.visible_properties:
                        try:
                            v = config.get_property_value(p.name)
                            log.info(f"  DeviceConfig [{type_id}] {p.name} = {v!r}")
                        except Exception:
                            pass
                    break
        except Exception:
            pass
        return _klient_instance.add_device(conn_str, config)

    feil_melding = ""
    try:
        device = _prøv_tilkobling(tilkobling)
    except Exception as e1:
        feil_melding = str(e1)
        log.warning(f"  {tilkobling} feila: {feil_melding}")
        device = None

        # Fallback: prøv alternativ protokoll
        if node.protokoll == "daq.opcua":
            alt = f"daq.nd://{node.adresse}:7420/"
        else:
            alt = f"daq.opcua://{node.adresse}:4840/"
        log.info(f"  Prøver fallback: {alt}")
        try:
            device = _prøv_tilkobling(alt)
        except Exception as e2:
            feil_melding = str(e2)
            log.warning(f"  Fallback {alt} feila òg: {feil_melding}")

    if device:
        with _hub_lock:
            _node_devices[node.id] = device
            n_ch = _tel_kanalar(device)
            _node_status[node.id] = {
                "tilkobla": True,
                "feil": None,
                "sist_sett": datetime.now().isoformat(),
                "tilkobla_sidan": datetime.now().isoformat(),
                "antal_kanalar": n_ch,
            }
            # Invalider cached readers — gamle peikar på dei døde signala frå
            # før reconnect. Lazy-rebuild ved neste hent_hub_kanalar/_les_fjern_verdiar.
            for k in list(_kanal_readers.keys()):
                if k[0] == node.id:
                    _kanal_readers.pop(k, None)
            for k in list(_relay_readers.keys()):
                if k[0] == node.id:
                    _relay_readers.pop(k, None)
        # Tøm status-cache så ny tilkobling vert reflektert i UI med ein gong
        with _status_cache_lock:
            _status_cache["ts"] = 0.0
        with _kanalar_cache_lock:
            _kanalar_cache["ts"] = 0.0
        log.info(f"  Tilkobla: '{node.namn}' — {n_ch} kanalar (readers invalidert)")

        # Diagnostikk: list sub-device info og streaming-kjelder
        try:
            info = device.info
            log.info(f"  Sub-device: namn={info.name}, serial={info.serial_number}")
            caps = info.server_capabilities
            for cap in caps:
                try:
                    pcs = cap.get_property_value("PrimaryConnectionString")
                    log.info(f"  Sub-device cap: {cap.protocol_id} → {pcs}")
                except Exception:
                    log.info(f"  Sub-device cap: {cap.protocol_id} port={cap.port}")
        except Exception as e:
            log.info(f"  Sub-device info utilgjengeleg: {e}")

        return True

    with _hub_lock:
        _node_devices.pop(node.id, None)
        _node_status[node.id] = {
            "tilkobla": False,
            "feil": feil_melding,
            "sist_sett": None,
            "tilkobla_sidan": None,
            "antal_kanalar": 0,
        }
    return False


def _fråkoble_node(node_id: str):
    """Fråkoble og fjern ein node frå klient-instansen (openDAQ) eller stopp modbus-tråd."""
    global _klient_instance, _node_devices, _node_status

    # Modbus: be manager stoppe polling for denne noden
    _modbus_manager.fjern_node(node_id)

    with _hub_lock:
        device = _node_devices.pop(node_id, None)
        _node_status.pop(node_id, None)
        # Invalider cached readers — peikar på signal frå gammal tilkobling
        for k in list(_kanal_readers.keys()):
            if k[0] == node_id:
                _kanal_readers.pop(k, None)
        for k in list(_relay_readers.keys()):
            if k[0] == node_id:
                _relay_readers.pop(k, None)

    if device and _klient_instance:
        try:
            _klient_instance.remove_device(device)
            log.info(f"  Fjerna device for node {node_id}")
        except Exception as e:
            log.warning(f"  Feil ved remove_device for {node_id}: {e}")


def _start_modbus_node(node: FjernNode) -> bool:
    """Legg til ein modbus-node i den delte ModbusManager-en."""
    ok = _modbus_manager.legg_til_node(node)
    # Oppdater _node_status med antal_kanalar (manager set dei andre feilta via callback)
    with _hub_lock:
        if node.id not in _node_status:
            _node_status[node.id] = {}
        _node_status[node.id]["antal_kanalar"] = len(node.modbus_registers)
    return ok


def _tel_kanalar(device) -> int:
    """Tel antal kanalar på ein openDAQ device."""
    try:
        channels = device.channels
        return len(channels) if channels else 0
    except Exception:
        return 0


# --- Kanal-readers cache for live-verdiar ---

_kanal_readers = {}  # (node_id, signal_id) -> StreamReader


_kanalar_cache = {"ts": 0.0, "data": []}
_kanalar_cache_lock = threading.Lock()
_KANALAR_CACHE_TTL = 0.75   # sekund — minskar CPU-last ved mange samtidige pollarar

_status_cache = {"ts": 0.0, "data": None}
_status_cache_lock = threading.Lock()
_STATUS_CACHE_TTL = 1.0

# Sett til True når node-konfig er endra utan restart — nye kanalar vises
# ikkje i DewesoftX før hub er restarta (flat RefDevice med låst NumberOfChannels).
_pending_changes = False


def hent_hub_kanalar() -> list:
    """Les kanal-metadata og siste verdi frå alle tilkobla nodar.

    Resultat vert cacha i ~750 ms for å unngå at fleire samtidige API-kall
    gjer duplikat openDAQ StreamReader-avlesingar.

    Brukar ein cached StreamReader per signal for å lese siste verdi.
    """
    # Cache-sjekk: returner tidlegare resultat viss det er nytt nok
    with _kanalar_cache_lock:
        if time.time() - _kanalar_cache["ts"] < _KANALAR_CACHE_TTL:
            return list(_kanalar_cache["data"])

    import opendaq as daq

    kanalar = []
    # Bruk timeout på _hub_lock — viss helsesjekk-tråden heng på proxy-kall,
    # returner stale cache i staden for å blokkere forever (→ CF 524).
    if not _hub_lock.acquire(timeout=2.0):
        log.warning("hent_hub_kanalar: _hub_lock timeout — returnerer cache")
        with _kanalar_cache_lock:
            return list(_kanalar_cache["data"])
    try:
        nodar_snapshot = list(_node_devices.items())
        konfig_nodar = {n.id: n for n in _hub_konfig.nodar}
        modbus_nodar = [n for n in _hub_konfig.nodar if n.type == NODE_TYPE_MODBUS_TCP]
    finally:
        _hub_lock.release()

    log.info(f"hent_hub_kanalar: {len(nodar_snapshot)} openDAQ-nodar tilkobla, "
             f"{len(modbus_nodar)} modbus-nodar")

    # Modbus-nodar: hent verdiar frå manager
    mb_verdiar = _modbus_manager.hent_verdiar()
    for node in modbus_nodar:
        node_status = _node_status.get(node.id, {})
        for ri, reg in enumerate(node.modbus_registers):
            verdi = mb_verdiar.get((node.id, reg.adresse))

            override_key = f"{node.id}:{reg.namn}"
            overstyrt = override_key in _kanal_range_overstyringer
            override = _kanal_range_overstyringer.get(override_key)
            if override:
                cr_low, cr_high = override
            else:
                RANGE_FAKTOR = 5.0
                max_abs = max(abs(reg.range_low), abs(reg.range_high), 1.0)
                cr_half = max_abs * RANGE_FAKTOR
                cr_low, cr_high = -cr_half, cr_half

            kanalar.append({
                "node_id": node.id,
                "node_namn": node.namn,
                "namn": reg.namn,
                "verdi": verdi,
                "eining": reg.eining,
                "auto_range_low": reg.range_low,
                "auto_range_high": reg.range_high,
                "cr_low": cr_low,
                "cr_high": cr_high,
                "overstyrt": overstyrt,
                "kanal_type": "modbus",
                "modbus_adresse": reg.adresse,
                "tilkobla": node_status.get("tilkobla", False),
            })

    # openDAQ-kanalar: iterer cached _fjern_kanal_info (ingen live device.channels-kall).
    # Kun StreamReader.read() vert gjort live, og den er lokal (ingen nettverk-proxy).
    for fk in _fjern_kanal_info:
        if fk.get("kanal_type") != "opendaq":
            continue
        node_id = fk.get("node_id", "")
        if not node_id:
            continue
        node_info = konfig_nodar.get(node_id)
        node_namn = node_info.namn if node_info else node_id
        ch_namn = fk.get("namn", "ukjent")
        sig_id = fk.get("sig_id", f"{node_id}_{fk.get('ch_idx', 0)}")

        # Live verdi for openDAQ-kanal vert primært tatt frå data-relay-tråden
        # sin DC-relay (sjå _les_fjern_verdiar). Vi gjer IKKJE StreamReader-
        # avlesing her — det blokkerer Flask-tråden ved overlasta Tailscale-
        # tilkoblingar (åpne 100k+ samples i buffer). UI viser verdi gjennom
        # /api/kanalar/live i staden, som kjem frå opendaq_bro.
        verdi = None

        auto_range_low = fk.get("range_low", -5.0)
        auto_range_high = fk.get("range_high", 5.0)
        cr_low = fk.get("cr_low", auto_range_low * 5.0)
        cr_high = fk.get("cr_high", auto_range_high * 5.0)

        override_key = f"{node_id}:{ch_namn}"
        overstyrt = override_key in _kanal_range_overstyringer

        kanalar.append({
            "node_id": node_id,
            "node_namn": node_namn,
            "namn": ch_namn,
            "verdi": verdi,
            "eining": fk.get("eining", ""),
            "auto_range_low": auto_range_low,
            "auto_range_high": auto_range_high,
            "cr_low": cr_low,
            "cr_high": cr_high,
            "overstyrt": overstyrt,
            "kanal_type": "opendaq",
            "tilkobla": node_id in dict(nodar_snapshot),
        })

    log.info(f"hent_hub_kanalar: Returnerer {len(kanalar)} kanalar "
             f"({sum(1 for k in kanalar if k['verdi'] is not None)} med verdi)")

    # Lagre i cache
    with _kanalar_cache_lock:
        _kanalar_cache["ts"] = time.time()
        _kanalar_cache["data"] = list(kanalar)

    return kanalar


def hent_kanal_ranges_dict() -> list:
    """Returner gjeldande kanal-range overstyringer som liste av dict."""
    overstyringer = les_kanal_ranges()
    return [o.til_dict() for o in overstyringer]


def oppdater_kanal_ranges(data: list) -> tuple:
    """Oppdater kanal-range overstyringer og last inn på nytt.

    Args:
        data: Liste av dict med node_id, kanal_namn, range_low, range_high, aktiv

    Returns:
        (ok, melding)
    """
    global _kanal_range_overstyringer

    overstyringer = []
    for i, o in enumerate(data):
        if not isinstance(o, dict):
            return False, f"Overstyring {i}: forventa objekt"
        node_id = str(o.get("node_id", "")).strip()
        kanal_namn = str(o.get("kanal_namn", "")).strip()
        if not node_id or not kanal_namn:
            return False, f"Overstyring {i}: 'node_id' og 'kanal_namn' kravd"
        try:
            range_low = float(o.get("range_low", 0))
            range_high = float(o.get("range_high", 0))
        except (TypeError, ValueError):
            return False, f"Overstyring {i}: ugyldig range-verdi"
        if range_low >= range_high:
            return False, f"Overstyring {i}: range_low ({range_low}) må vere mindre enn range_high ({range_high})"
        aktiv = bool(o.get("aktiv", True))
        overstyringer.append(KanalRangeOverstyring(
            node_id=node_id,
            kanal_namn=kanal_namn,
            range_low=range_low,
            range_high=range_high,
            aktiv=aktiv,
        ))

    ok = lagre_kanal_ranges(overstyringer)
    if ok:
        _kanal_range_overstyringer = hent_range_map(overstyringer)
        log.info(f"Kanal-range overstyringer oppdatert: {len(_kanal_range_overstyringer)} aktive")
        return True, f"{len(overstyringer)} overstyring(ar) lagra — rekoble for å aktivere"
    return False, "Kunne ikkje lagre kanal-range overstyringer"


def _start_serverar():
    """Start NativeStreaming + OPC-UA serverar på hub-instansen.

    Same oppsett som node-modus (opendaq_bro.py):
      - NativeStreaming FYRST (port 7420, data + NativeConfiguration)
      - OPC-UA SIST (port 4840, metadata — krevd internt av NativeStreaming)
      - Discovery BERRE på NativeStreaming (ikkje OPC-UA, unngår duplikat)

    Tidlegare var OPC-UA utelatt, men NativeStreaming sin NativeConfiguration
    treng OPC-UA internt for metadata-transport. Utan OPC-UA vert
    PrimaryConnectionString feil: daq://Dewesoft_DB00000001 i staden for
    daq.nd://IP:7420/. No har hub root device (daqref://device0) som
    node-modus, so begge serverar deler same device → ingen duplikat.
    """
    global _instance

    ip = os.environ.get("OPENDAQ_IP", "")
    servere = []
    servers_added = []  # (srv_type, server_obj)

    # Start NativeStreaming FYRST, deretter OPC-UA (openDAQ-dokumentasjon)
    for srv_type in ['OpenDAQNativeStreaming', 'OpenDAQOPCUA']:
        try:
            config = None
            try:
                srv_type_obj = _instance.available_server_types.get(srv_type)
                if srv_type_obj:
                    config = srv_type_obj.create_default_config()
            except Exception:
                config = None

            server = _instance.add_server(srv_type, config)
            servers_added.append((srv_type, server))
            servere.append(srv_type)
            log.info(f"  Server starta: {srv_type}")
        except Exception as e:
            log.warning(f"  {srv_type} feilet: {e}")

    # Fiks PrimaryConnectionString for multi-IP Pi (WiFi + LAN)
    if ip:
        _fiks_primary_connection_strings(ip)

    # Fiks nil string-eigenskapar (DewesoftX krasjar med 'Interface object is nil')
    _fiks_nil_strings()

    # Fiks stale OPC-UA verdiar (C++ writeValue-callback feiler,
    # so OPC-UA-noden beheld default verdiar sjølv etter Python set_property_value)
    if 'OpenDAQOPCUA' in servere:
        _fiks_opcua_verdiar()
        _logg_opcua_tre()

    # Aktiver mDNS discovery BERRE på NativeStreaming.
    # OPC-UA skal IKKJE annonserast — DewesoftX finn den via port-scanning
    # og ville lagt til ei ekstra eining (duplikat).
    for srv_type, server in servers_added:
        if srv_type == 'OpenDAQOPCUA':
            log.info(f"  {srv_type}: discovery IKKJE aktivert (unngår duplikat)")
            continue
        try:
            server.enable_discovery()
            log.info(f"  {srv_type}: discovery aktivert")
        except Exception as e_disc:
            log.warning(f"  {srv_type}: enable_discovery feilet: {e_disc}")

    log.info(f"Hub-serverar aktive: {servere}")
    return servere


def _fiks_primary_connection_strings(ip):
    """Sett PrimaryConnectionString på alle ServerCapabilities til riktig IP.

    Pi med to nettverksinterface kan føre til at NativeStreaming-serveren
    vel ein annan IP enn OPC-UA. DewesoftX finn eininga via mDNS (éin IP),
    men NativeStreaming-cap kan peike til feil IP.
    """
    global _instance
    try:
        caps = _instance.info.server_capabilities
        for cap in caps:
            proto_id = cap.protocol_id
            prefix = cap.prefix
            port = cap.port

            ny_conn = f"{prefix}://{ip}:{port}/"
            try:
                noverande = cap.get_property_value("PrimaryConnectionString")
            except Exception:
                noverande = ""

            if ip in str(noverande):
                log.info(f"  Cap {proto_id}: PrimaryConnectionString OK ({noverande})")
                continue

            try:
                cap.set_property_value("PrimaryConnectionString", ny_conn)
                log.info(f"  Cap {proto_id}: PrimaryConnectionString → {ny_conn}")
            except Exception as e:
                log.warning(f"  Cap {proto_id}: Kunne ikkje sette PrimaryConnectionString: {e}")
    except Exception as e:
        log.warning(f"  _fiks_primary_connection_strings feilet: {e}")


def _fiks_nil_strings():
    """Sett nil string-eigenskapar til tom streng.

    DewesoftX krasjar med 'Interface object is nil' i InitStringProperty
    når ein string-eigenskap har nil-verdi (nullptr) i staden for "".
    Same som opendaq_bro._fiks_alle_nil_strings().
    """
    global _instance
    import opendaq as daq
    ct = getattr(daq, 'CoreType', None)
    if ct is None:
        return

    def _fiks_obj(obj, label):
        try:
            for prop in obj.visible_properties:
                try:
                    if prop.value_type == ct.ctString:
                        try:
                            val = obj.get_property_value(prop.name)
                        except Exception:
                            val = None
                        if val is None:
                            try:
                                obj.set_property_value(prop.name, "")
                                log.info(f"  {label}{prop.name}: nil → ''")
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"  _fiks_nil_strings({label}): {e}")

    # Device-eigenskapar
    _fiks_obj(_instance, "Dev.")

    # DeviceInfo-eigenskapar
    try:
        info = _instance.info
        if info:
            _fiks_obj(info, "Info.")
    except Exception:
        pass

    # Kanal-eigenskapar
    try:
        for i, ch in enumerate(_instance.channels):
            _fiks_obj(ch, f"Ch{i}.")
    except Exception:
        pass

    # MERK: Sub-device (fjern-nodar) eigenskapar vert IKKJE fiksa her.
    # Sub-device properties går gjennom OPC-UA klient til remote node,
    # og kan trigge "terminate called" (SIGABRT) om tilkoblinga har tidsavbrot.
    # Nil strings på fjern-nodar må fiksast på REMOTE-noden (opendaq_bro.py).

    log.info("  Nil string-eigenskapar fiksa (berre root device)")


def _fiks_opcua_verdiar():
    """Skriv stale OPC-UA eigenskapar direkte via asyncua.

    C++ writeValue()-callbacken feiler med 'DataType incompatible'.
    Python SDK set_property_value() endrar intern state, men OPC-UA-noden
    beheld gammal verdi. DewesoftX les OPC-UA via NativeConfiguration
    og får feil verdiar → feil connection string, disconnected.

    Same logikk som opendaq_bro._fiks_opcua_verdiar().
    """
    try:
        import asyncio
        from asyncua import Client as OpcClient, ua as opcua
    except ImportError:
        log.warning("  asyncua ikkje installert — kan ikkje fikse OPC-UA verdiar")
        return

    # Tel totalt antal kanalar frå alle tilkobla fjern-nodar
    n_kanalar = 0
    with _hub_lock:
        for nid, dev in _node_devices.items():
            n_kanalar += _tel_kanalar(dev)
    # Minimum 1 kanal for at DewesoftX skal akseptere eininga
    if n_kanalar < 1:
        n_kanalar = 1
    log.info(f"  OPC-UA fiks: brukar n_kanalar={n_kanalar} (frå fjern-nodar)")
    sr = float(os.environ.get("SAMPLE_RATE", "20000"))

    async def _skriv():
        c = OpcClient("opc.tcp://127.0.0.1:4840", timeout=5)
        await c.connect()
        fiksa = 0
        try:
            # NumberOfChannels
            node = c.get_node(opcua.NodeId("/RefDev0/NumberOfChannels", 4))
            old = await node.read_value()
            written = False
            for vtype in (opcua.VariantType.Int64, opcua.VariantType.Int32):
                try:
                    dv = opcua.DataValue(opcua.Variant(n_kanalar, vtype))
                    await node.write_value(dv)
                    written = True
                    if old != n_kanalar:
                        log.info(f"  OPC-UA fiks: NumberOfChannels {old} → {n_kanalar} ({vtype})")
                        fiksa += 1
                    else:
                        log.info(f"  OPC-UA fiks: NumberOfChannels stadfesta {n_kanalar} ({vtype})")
                    break
                except Exception as e_type:
                    log.info(f"  OPC-UA NumberOfChannels {vtype} avvist: {e_type}")
            if not written:
                log.warning(f"  OPC-UA fiks: kunne ikkje skrive NumberOfChannels={n_kanalar}")

            # GlobalSampleRate
            sr_node = c.get_node(opcua.NodeId("/RefDev0/GlobalSampleRate", 4))
            old_sr = await sr_node.read_value()
            if abs(old_sr - sr) > 0.1:
                dv = opcua.DataValue(opcua.Variant(sr, opcua.VariantType.Double))
                await sr_node.write_value(dv)
                log.info(f"  OPC-UA fiks: GlobalSampleRate {old_sr} → {sr}")
                fiksa += 1
        except Exception as e:
            log.warning(f"  OPC-UA fiks feilet: {e}")
        finally:
            await c.disconnect()
        return fiksa

    try:
        loop = asyncio.new_event_loop()
        n = loop.run_until_complete(_skriv())
        loop.close()
        if n:
            log.info(f"  OPC-UA: {n} stale verdiar fiksa")
    except Exception as e:
        log.warning(f"  OPC-UA fixup feilet: {e}")


def _logg_opcua_tre():
    """Browse OPC-UA treet og logg kva DewesoftX ser (diagnostikk).

    Berre server-instansen (_instance) — ingen sub-devices.
    """
    try:
        import asyncio
        from asyncua import Client as OpcClient, ua as opcua
    except ImportError:
        return

    async def _browse():
        c = OpcClient("opc.tcp://127.0.0.1:4840", timeout=5)
        await c.connect()
        try:
            root = c.get_node(opcua.NodeId("/RefDev0", 4))
            children = await root.get_children()
            log.info(f"  OPC-UA tre /RefDev0/: {len(children)} barn")

            for child in children:
                browse_name = await child.read_browse_name()
                namn = browse_name.Name
                # Logg berre nokre viktige nodar, ikkje heile treet
                if namn in ("NumberOfChannels", "GlobalSampleRate", "IO",
                            "Dev", "Sig", "FB", "IP"):
                    try:
                        val = await child.read_value()
                        log.info(f"  OPC-UA /RefDev0/{namn} = {repr(val)[:80]}")
                    except Exception:
                        sub_count = len(await child.get_children())
                        log.info(f"  OPC-UA /RefDev0/{namn} ({sub_count} barn)")
                else:
                    log.info(f"  OPC-UA /RefDev0/{namn}")
        except Exception as e:
            log.info(f"  OPC-UA tre-browse feilet: {e}")
        finally:
            await c.disconnect()

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_browse())
        loop.close()
    except Exception as e:
        log.info(f"  OPC-UA tre-diagnostikk feilet: {e}")


# --- Helsesjekk-løkke ---

def _helsesjekk_loop():
    """Bakgrunnstråd som sjekkar om fjern-nodar er tilgjengelege."""
    global _helsesjekk_aktiv

    konfig = _hub_konfig
    intervall = konfig.helsesjekk_intervall
    reconnect_intervall = konfig.reconnect_intervall
    _siste_rekobling = {}  # node_id -> timestamp

    while _helsesjekk_aktiv:
        time.sleep(intervall)
        if not _helsesjekk_aktiv:
            break

        for node in konfig.nodar:
            if not node.aktivert:
                continue

            # Modbus-nodar har eigen polling-tråd som handterer reconnect
            if node.type == NODE_TYPE_MODBUS_TCP:
                continue

            with _hub_lock:
                device = _node_devices.get(node.id)
                status = _node_status.get(node.id, {})

            if device and status.get("tilkobla"):
                # Sjekk om device framleis er tilgjengeleg. Gjer proxy-kalla
                # UTAN å halde _hub_lock — ellers blokkerer vi alle API-requests
                # viss noden heng (sett CF tunnel 524 timeouts).
                try:
                    _ = device.info.name
                    n_ch = _tel_kanalar(device)
                    with _hub_lock:
                        if node.id in _node_status:
                            _node_status[node.id]["sist_sett"] = datetime.now().isoformat()
                            _node_status[node.id]["antal_kanalar"] = n_ch
                except Exception as e:
                    log.warning(f"Helsesjekk: '{node.namn}' fråkobla: {e}")
                    with _hub_lock:
                        if node.id in _node_status:
                            _node_status[node.id]["tilkobla"] = False
                            _node_status[node.id]["feil"] = str(e)
                        _node_devices.pop(node.id, None)
                        # Invalider readers — gamle peikar på dei døde signala
                        for k in list(_kanal_readers.keys()):
                            if k[0] == node.id:
                                _kanal_readers.pop(k, None)
                        for k in list(_relay_readers.keys()):
                            if k[0] == node.id:
                                _relay_readers.pop(k, None)
                    # Prøv remove_device (utan lås — kan blokkere)
                    try:
                        _klient_instance.remove_device(device)
                    except Exception:
                        pass
            else:
                # Ikkje tilkobla — prøv rekobling med intervall
                siste = _siste_rekobling.get(node.id, 0)
                if time.time() - siste >= reconnect_intervall:
                    _siste_rekobling[node.id] = time.time()
                    log.info(f"Prøver rekobling til '{node.namn}'...")
                    _koble_til_node(node)


# --- API-funksjonar for web_ui ---

def hent_hub_status() -> dict:
    """Returnerer komplett hub-status med per-node info.

    Cacha i ~1s for å redusere _hub_lock-contention. Frontend pollar denne
    ofte og den vert ikkje mykje nyare innan ein sekund.
    """
    with _status_cache_lock:
        if _status_cache["data"] is not None and time.time() - _status_cache["ts"] < _STATUS_CACHE_TTL:
            return dict(_status_cache["data"])

    # Timeout på lock for å unngå at treg helsesjekk blokkerer API-et
    if not _hub_lock.acquire(timeout=2.0):
        log.warning("hent_hub_status: _hub_lock timeout — returnerer cache")
        with _status_cache_lock:
            if _status_cache["data"] is not None:
                return dict(_status_cache["data"])
        return {"modus": "hub", "nodar": [], "feil": "lock-timeout"}
    try:
        nodar_info = []
        for node in _hub_konfig.nodar:
            status = _node_status.get(node.id, {})
            # Modbus: antal kanalar = antal register (uavhengig av tilkobling)
            if node.type == NODE_TYPE_MODBUS_TCP:
                antal_kanalar = len(node.modbus_registers)
            else:
                antal_kanalar = status.get("antal_kanalar", 0)
            nodar_info.append({
                "id": node.id,
                "namn": node.namn,
                "adresse": node.adresse,
                "port": node.port,
                "protokoll": node.protokoll,
                "lokasjon": node.lokasjon,
                "aktivert": node.aktivert,
                "type": node.type,
                "modbus_unit_id": node.modbus_unit_id,
                "modbus_poll_hz": node.modbus_poll_hz,
                "modbus_timeout_ms": node.modbus_timeout_ms,
                "modbus_registers": [r.til_dict() for r in node.modbus_registers],
                "tilkobla": status.get("tilkobla", False),
                "feil": status.get("feil"),
                "sist_sett": status.get("sist_sett"),
                "tilkobla_sidan": status.get("tilkobla_sidan"),
                "antal_kanalar": antal_kanalar,
            })

        totalt_kanalar = sum(n.get("antal_kanalar", 0) for n in nodar_info)
        tilkobla_antal = sum(1 for n in nodar_info if n.get("tilkobla"))
    finally:
        _hub_lock.release()

    resultat = {
        "modus": "hub",
        "startet": _hub_startet,
        "totalt_kanalar": totalt_kanalar,
        "totalt_nodar": len(nodar_info),
        "tilkobla_nodar": tilkobla_antal,
        "nodar": nodar_info,
        "ip": os.environ.get("OPENDAQ_IP", _hent_ip()),
        "pending_changes": _pending_changes,
    }
    with _status_cache_lock:
        _status_cache["ts"] = time.time()
        _status_cache["data"] = dict(resultat)
    return resultat


def hent_hub_konfig_dict() -> dict:
    """Returnerer hub-konfig som dict for API."""
    with _hub_lock:
        return _hub_konfig.til_dict()


def hent_hub_buffer_status() -> dict:
    """Returner hub-buffer status for web API."""
    if _hub_buffer is not None:
        return _hub_buffer.hent_status()
    return {"aktivert": False, "totalt_rader": 0}


def oppdater_hub_konfig(ny_konfig: HubKonfig) -> tuple:
    """Oppdater og synkroniser hub-konfig. Returns (suksess, melding)."""
    global _hub_konfig, _pending_changes

    ok = lagre_hub_konfig(ny_konfig)
    if not ok:
        return False, "Kunne ikkje lagre konfig"

    # Synkroniser tilkoblingar
    with _hub_lock:
        gamle_ids = set(n.id for n in _hub_konfig.nodar)
        nye_ids = set(n.id for n in ny_konfig.nodar)
        _hub_konfig = ny_konfig

    # Fjern nodar som ikkje lenger er i konfig
    for fjerna_id in (gamle_ids - nye_ids):
        _fråkoble_node(fjerna_id)

    # Koble til nye nodar
    new_opendaq_nodes = False
    for node in ny_konfig.nodar:
        if node.id not in gamle_ids and node.aktivert:
            _koble_til_node(node)
            if node.type != NODE_TYPE_MODBUS_TCP:
                new_opendaq_nodes = True
    # Viss openDAQ-nodar har vorte lagt til eller fjerna, trengs restart
    removed_opendaq = any(
        n.type != NODE_TYPE_MODBUS_TCP
        for n in _hub_konfig.nodar if n.id in (gamle_ids - nye_ids)
    )
    if new_opendaq_nodes or removed_opendaq:
        _pending_changes = True

    melding = f"Konfig oppdatert ({len(ny_konfig.nodar)} nodar)"
    if _pending_changes:
        melding += " — restart hub for å aktivere"
    return True, melding


def legg_til_node_api(data: dict) -> tuple:
    """Legg til ein ny node og koble til. Returns (suksess, melding, node_dict).

    Brukar `valider_hub_konfig` for validering ved å wrappe i ein midlertidig
    konfig-dict med berre den nye noden. Slik får modbus-nodar full validering
    gratis (register-liste, funksjonar, datatypar etc.).
    """
    import uuid as _uuid

    if not isinstance(data, dict):
        return False, "Forventa objekt", None

    node_dict = dict(data)
    if "id" not in node_dict:
        node_dict["id"] = _uuid.uuid4().hex[:8]
    if not str(node_dict.get("adresse", "")).strip():
        return False, "Mangler 'adresse'", None

    # Valider via eksisterande validator
    validert, feil = valider_hub_konfig({"nodar": [node_dict]})
    if feil:
        return False, feil, None

    node = validert.nodar[0]

    with _hub_lock:
        _hub_konfig.nodar.append(node)
    lagre_hub_konfig(_hub_konfig)

    ok = _koble_til_node(node)
    global _pending_changes
    if node.type != NODE_TYPE_MODBUS_TCP:
        # openDAQ-nodar krev hub-restart for at kanalar skal vises i DewesoftX
        _pending_changes = True
    status_tekst = "tilkobla" if ok else "lagt til (tilkobling feila)"
    melding = f"Node '{node.namn}' {status_tekst}"
    if _pending_changes:
        melding += " — restart hub for å aktivere kanalar i DewesoftX"
    return True, melding, node.til_dict()


def fjern_node_api(node_id: str) -> tuple:
    """Fjern ein node. Returns (suksess, melding)."""
    global _hub_konfig, _pending_changes

    with _hub_lock:
        node = next((n for n in _hub_konfig.nodar if n.id == node_id), None)
        if not node:
            return False, f"Node '{node_id}' ikkje funnen"
        _hub_konfig.nodar = [n for n in _hub_konfig.nodar if n.id != node_id]

    _fråkoble_node(node_id)
    lagre_hub_konfig(_hub_konfig)
    if node.type != NODE_TYPE_MODBUS_TCP:
        _pending_changes = True
    return True, f"Node '{node.namn}' fjerna"


def restart_hub() -> tuple:
    """Restart hub-prosessen via os.execv.

    Brukt når node-konfig er endra og nye kanalar må eksponerast i
    RefDevice. openDAQ har ingen reint hot-reload for NumberOfChannels,
    så full restart er einaste pålitelege måte. DewesoftX mister
    tilkobling ~10 sek, deretter rekoblar den automatisk.

    Returnerer ikkje i praksis — execv erstattar prosessen.
    """
    log.warning("RESTART: hub-prosess re-startar via execv")
    try:
        # Kort forsinking så HTTP-responsen kan sendast ferdig
        def _do_restart():
            time.sleep(1.0)
            try:
                # os.execv erstattar prosessen med 'python3 -m hub_server'
                python_exe = sys.executable or "/usr/bin/python3"
                os.execv(python_exe, [python_exe, "-m", "hub_server"])
            except Exception as e:
                log.error(f"execv feila: {e} — prøver os._exit(0) i staden")
                os._exit(0)  # Container vil rekøyre entrypoint
        threading.Thread(target=_do_restart, daemon=True).start()
        return True, "Hub restartar om 1 sekund — rekoble nettlesar etter ~10 sek"
    except Exception as e:
        return False, f"Restart feila: {e}"


def rekoble_node(node_id: str) -> tuple:
    """Tving rekobling av ein node. Returns (suksess, melding)."""
    with _hub_lock:
        node = next((n for n in _hub_konfig.nodar if n.id == node_id), None)
    if not node:
        return False, f"Node '{node_id}' ikkje funnen"

    # Fjern eksisterande tilkobling
    _fråkoble_node(node_id)

    # Koble til på nytt
    ok = _koble_til_node(node)
    if ok:
        return True, f"Rekobla til '{node.namn}'"
    return False, f"Rekobling til '{node.namn}' feila"


def _hent_ip() -> str:
    """Finn maskinens IP-adresse."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "ukjent"


# --- Oppstart ---

def start_hub():
    """Hovudoppstart for hub-modus."""
    global _hub_konfig, _hub_startet, _kanal_range_overstyringer

    log.info("=" * 60)
    log.info("  openDAQ Hub — Aggregator")
    log.info("=" * 60)

    # Les konfig
    _hub_konfig = les_hub_konfig()
    log.info(f"  Nodar konfigurert: {len(_hub_konfig.nodar)}")
    log.info(f"  Helsesjekk-intervall: {_hub_konfig.helsesjekk_intervall}s")
    log.info(f"  Reconnect-intervall: {_hub_konfig.reconnect_intervall}s")

    # Les kanal-range overstyringer
    overstyringer = les_kanal_ranges()
    _kanal_range_overstyringer = hent_range_map(overstyringer)
    if _kanal_range_overstyringer:
        log.info(f"  Kanal-range overstyringer: {len(_kanal_range_overstyringer)}")

    # Opprett openDAQ Instance
    _opprett_instance()

    # Start modbus-manager (pollar alle modbus-nodar parallelt)
    _modbus_manager.start(_hub_konfig.nodar)

    # Koble til aktive openDAQ-nodar (modbus er alt starta av manager ovanfor)
    for node in _hub_konfig.nodar:
        if not node.aktivert:
            log.info(f"  Node '{node.namn}' deaktivert — hoppar over")
            continue
        if node.type == NODE_TYPE_MODBUS_TCP:
            # Set antal_kanalar + tilkobla-status manuelt (manager brukar callback)
            with _hub_lock:
                if node.id not in _node_status:
                    _node_status[node.id] = {}
                _node_status[node.id]["antal_kanalar"] = len(node.modbus_registers)
            continue
        _koble_til_node(node)

    # Oppdater root device kanalar til å matche fjern-nodar.
    # Må skje FØR server-start slik at OPC-UA/NativeStreaming
    # eksponerer rett antal kanalar frå starten.
    _oppdater_root_kanalar()

    # Start serverar
    _start_serverar()

    _hub_startet = datetime.now().isoformat()

    # Initialiser DataPacket-injeksjon (signal descriptors + acqLoop toggle).
    # Må skje ETTER server-start — ISignalConfig krev fullt initialiserte signal.
    time.sleep(1)
    _init_data_injeksjon()

    # Start helsesjekk-tråd
    helsesjekk_traad = threading.Thread(target=_helsesjekk_loop, daemon=True)
    helsesjekk_traad.start()

    # Start data-relay tråd (DataPacket-injeksjon eller DC-fallback)
    relay_traad = threading.Thread(target=_data_relay_loop, daemon=True)
    relay_traad.start()
    log.info("Helsesjekk- og relay-trådar starta")

    # Start hub-buffer sync
    global _hub_buffer
    try:
        buffer_konfig = les_buffer_konfig()
        _hub_buffer = HubBuffer(buffer_konfig)
        _hub_buffer.start_sync(_hub_konfig.nodar)
        log.info(f"Hub-buffer sync starta: intervall={buffer_konfig.hub_sync_intervall_sek}s")
    except Exception as e:
        log.warning(f"Kunne ikkje starte hub-buffer: {e}")

    # Start web UI
    web_port = int(os.environ.get("WEB_PORT", 8080))
    log.info(f"Startar web UI på port {web_port}...")

    from web_ui import app as flask_app
    flask_app.run(host="0.0.0.0", port=web_port, use_reloader=False, threaded=True)


def main():
    start_hub()


if __name__ == "__main__":
    main()
