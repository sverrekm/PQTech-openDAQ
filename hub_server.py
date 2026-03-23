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
)
from buffer_konfig import les_buffer_konfig
from hub_buffer import HubBuffer

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
    """
    global _instance, _kanal_mapping

    import opendaq as daq

    # --- Hent fjern-kanal info ---
    fjern_kanalar = []  # [{namn, range_low, range_high, node_id, ch_idx}]
    with _hub_lock:
        # Stabil rekkefølge: same som _hub_konfig.nodar
        for node in _hub_konfig.nodar:
            dev = _node_devices.get(node.id)
            if not dev:
                continue
            try:
                channels = dev.channels
                for ci in range(len(channels)):
                    ch = channels[ci]
                    namn = "ukjent"
                    range_low, range_high = -5.0, 5.0
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
                        # Fallback: signal descriptor
                        try:
                            sig = ch.signals[0]
                            desc = sig.descriptor
                            if desc and desc.value_range:
                                range_low = float(desc.value_range.low_value)
                                range_high = float(desc.value_range.high_value)
                        except Exception:
                            pass
                    fjern_kanalar.append({
                        "namn": namn,
                        "range_low": range_low,
                        "range_high": range_high,
                        "node_id": node.id,
                        "ch_idx": ci,
                    })
            except Exception as e:
                log.warning(f"  Kunne ikkje lese kanalar frå '{node.namn}': {e}")

    n_kanalar = len(fjern_kanalar)
    if n_kanalar < 1:
        n_kanalar = 1
        fjern_kanalar = [{"namn": "AI 0", "range_low": -5.0, "range_high": 5.0,
                          "node_id": "", "ch_idx": 0}]
        log.info("  Ingen fjern-kanalar — brukar 1 dummy-kanal")
    else:
        log.info(f"  {n_kanalar} fjern-kanalar funne")

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

        # Amplitude=0, Frequency=min (0.1) → flat linje ved DC-verdi
        _safe_set(ch, "Amplitude", 0.0)
        _safe_set(ch, "Frequency", 0.1)
        _safe_set(ch, "DC", 0.0)
        _safe_set(ch, "Waveform", 0)

        # CustomRange — same som remote kanal
        r_low = fk["range_low"]
        r_high = fk["range_high"]
        try:
            custom_range = daq.Range(r_low, r_high)
            ch.set_property_value("CustomRange", custom_range)
        except Exception as e:
            log.warning(f"  CustomRange({r_low}, {r_high}) for '{fk['namn']}': {e}")

        # Skaleringsdata for DC relay:
        # CustomRange mappar intern [-10,10] til fysisk [r_low, r_high]
        # Intern DC = (fysisk_verdi - offset) / scale
        span = r_high - r_low
        scale = span / 20.0 if span > 0 else 1.0   # 20 = intern span [-10,10]
        offset = (r_high + r_low) / 2.0
        _kanal_mapping.append((fk["node_id"], fk["ch_idx"], scale, offset))

        # GetPossibleSampleRate — DewesoftX krev dette på KVAR kanal
        try:
            try:
                ch.get_property("GetPossibleSampleRate")
            except Exception:
                prop = daq.FloatPropertyBuilder("GetPossibleSampleRate", 200000.0)
                ch.add_property(prop.build())
        except Exception:
            pass

        log.info(f"  Kanal {i}: '{fk['namn']}' range=[{r_low}, {r_high}] "
                 f"scale={scale:.1f} offset={offset:.1f}")

    log.info(f"  {len(_kanal_mapping)} hub-kanalar konfigurert")


def _data_relay_loop():
    """Bakgrunnstråd: les verdiar frå fjern-nodar og oppdater hub-kanalar.

    Les siste verdi frå kvar remote kanal via StreamReader,
    skalerer til intern DC-range, og set DC på tilsvarande hub-kanal.
    Gir DewesoftX tilnærma sanntidsdata frå remote nodar.
    """
    global _relay_aktiv

    import opendaq as daq

    log.info("Data-relay tråd starta")
    time.sleep(2)  # Vent til serverar er starta

    while _relay_aktiv:
        time.sleep(0.5)  # Oppdater 2 gonger per sekund

        try:
            hub_channels = list(_instance.channels)
        except Exception:
            continue

        with _hub_lock:
            devices = dict(_node_devices)

        for hub_idx, (nid, remote_idx, scale, offset) in enumerate(_kanal_mapping):
            if hub_idx >= len(hub_channels):
                break
            if not nid:  # Dummy-kanal
                continue

            dev = devices.get(nid)
            if not dev:
                continue

            try:
                remote_ch = dev.channels[remote_idx]
                signals = remote_ch.signals
                if not signals or len(signals) == 0:
                    continue
                sig = signals[0]

                # Lag eller gjenbruk StreamReader
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
                if count > 0:
                    values = reader.read(count)
                    if values is not None and len(values) > 0:
                        physical_val = float(values[-1])
                        # Skaler til intern DC-range: DC = (physical - offset) / scale
                        if scale != 0:
                            dc_val = (physical_val - offset) / scale
                        else:
                            dc_val = physical_val
                        _safe_set(hub_channels[hub_idx], "DC", dc_val)
            except Exception:
                pass


def _koble_til_node(node: FjernNode) -> bool:
    """Prøv å koble til ein fjern-node via add_device().

    OPC-UA for konfig + openDAQ oppdagar NativeStreaming automatisk.
    daq.nd:// (NativeConfiguration) er deaktivert på remote-nodar
    (opendaq_bro.py linje 1592) — bruk daq.opcua:// som standard.
    """
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
        log.info(f"  Tilkobla: '{node.namn}' — {n_ch} kanalar")

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
    """Fråkoble og fjern ein node frå klient-instansen."""
    global _klient_instance, _node_devices, _node_status

    with _hub_lock:
        device = _node_devices.pop(node_id, None)
        _node_status.pop(node_id, None)

    if device and _klient_instance:
        try:
            _klient_instance.remove_device(device)
            log.info(f"  Fjerna device for node {node_id}")
        except Exception as e:
            log.warning(f"  Feil ved remove_device for {node_id}: {e}")


def _tel_kanalar(device) -> int:
    """Tel antal kanalar på ein openDAQ device."""
    try:
        channels = device.channels
        return len(channels) if channels else 0
    except Exception:
        return 0


# --- Kanal-readers cache for live-verdiar ---

_kanal_readers = {}  # (node_id, signal_id) -> StreamReader


def hent_hub_kanalar() -> list:
    """Les kanal-metadata og siste verdi frå alle tilkobla nodar.

    Brukar ein cached StreamReader per signal for å lese siste verdi.
    """
    import opendaq as daq

    kanalar = []
    with _hub_lock:
        nodar_snapshot = list(_node_devices.items())
        konfig_nodar = {n.id: n for n in _hub_konfig.nodar}

    log.info(f"hent_hub_kanalar: {len(nodar_snapshot)} nodar tilkobla")

    for node_id, device in nodar_snapshot:
        node_info = konfig_nodar.get(node_id)
        node_namn = node_info.namn if node_info else node_id

        try:
            channels = device.channels
        except Exception as e:
            log.info(f"hent_hub_kanalar: Kan ikkje lese channels frå '{node_namn}': {e}")
            continue

        try:
            ch_count = len(channels)
        except Exception:
            ch_count = 0

        log.info(f"hent_hub_kanalar: Node '{node_namn}' — {ch_count} kanalar")

        if ch_count == 0:
            continue

        for idx in range(ch_count):
            try:
                ch = channels[idx]
            except Exception as e:
                log.info(f"hent_hub_kanalar: Kan ikkje hente kanal [{idx}] frå '{node_namn}': {e}")
                continue

            try:
                ch_namn = ch.name
            except Exception:
                ch_namn = "ukjent"

            # Hent signal og descriptor
            eining = ""
            verdi = None
            try:
                signals = ch.signals
                sig_count = len(signals) if signals else 0

                if sig_count > 0:
                    sig = signals[0]
                    try:
                        sig_id = sig.global_id
                    except Exception:
                        sig_id = f"{node_id}_{ch_namn}_{idx}"

                    # Eining frå descriptor
                    try:
                        desc = sig.descriptor
                        if desc and desc.unit:
                            eining = desc.unit.symbol or ""
                    except Exception:
                        pass

                    # Les siste verdi via cached StreamReader
                    reader_key = (node_id, sig_id)
                    if reader_key not in _kanal_readers:
                        try:
                            _kanal_readers[reader_key] = daq.StreamReader(sig)
                            log.info(f"hent_hub_kanalar: Oppretta StreamReader for '{ch_namn}'")
                        except Exception as e:
                            log.warning(f"hent_hub_kanalar: StreamReader feil for '{ch_namn}': {e}")

                    reader = _kanal_readers.get(reader_key)
                    if reader:
                        try:
                            count = reader.available_count
                            if count > 0:
                                values = reader.read(count)
                                if values is not None and len(values) > 0:
                                    verdi = float(values[-1])
                        except Exception as e:
                            log.warning(f"hent_hub_kanalar: Lesefeil for '{ch_namn}': {e}")
                            _kanal_readers.pop(reader_key, None)
                else:
                    log.info(f"hent_hub_kanalar: Kanal '{ch_namn}' har 0 signal")
            except Exception as e:
                log.info(f"hent_hub_kanalar: Signal-feil for '{ch_namn}': {e}")

            kanalar.append({
                "node_id": node_id,
                "node_namn": node_namn,
                "namn": ch_namn,
                "verdi": verdi,
                "eining": eining,
            })

    log.info(f"hent_hub_kanalar: Returnerer {len(kanalar)} kanalar "
             f"({sum(1 for k in kanalar if k['verdi'] is not None)} med verdi)")
    return kanalar


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

            with _hub_lock:
                device = _node_devices.get(node.id)
                status = _node_status.get(node.id, {})

            if device and status.get("tilkobla"):
                # Sjekk om device framleis er tilgjengeleg
                try:
                    _ = device.info.name
                    with _hub_lock:
                        _node_status[node.id]["sist_sett"] = datetime.now().isoformat()
                        _node_status[node.id]["antal_kanalar"] = _tel_kanalar(device)
                except Exception as e:
                    log.warning(f"Helsesjekk: '{node.namn}' fråkobla: {e}")
                    with _hub_lock:
                        _node_status[node.id]["tilkobla"] = False
                        _node_status[node.id]["feil"] = str(e)
                    # Prøv remove_device frå klient-instansen
                    try:
                        _klient_instance.remove_device(device)
                    except Exception:
                        pass
                    with _hub_lock:
                        _node_devices.pop(node.id, None)
            else:
                # Ikkje tilkobla — prøv rekobling med intervall
                siste = _siste_rekobling.get(node.id, 0)
                if time.time() - siste >= reconnect_intervall:
                    _siste_rekobling[node.id] = time.time()
                    log.info(f"Prøver rekobling til '{node.namn}'...")
                    _koble_til_node(node)


# --- API-funksjonar for web_ui ---

def hent_hub_status() -> dict:
    """Returnerer komplett hub-status med per-node info."""
    with _hub_lock:
        nodar_info = []
        for node in _hub_konfig.nodar:
            status = _node_status.get(node.id, {})
            nodar_info.append({
                "id": node.id,
                "namn": node.namn,
                "adresse": node.adresse,
                "port": node.port,
                "protokoll": node.protokoll,
                "lokasjon": node.lokasjon,
                "aktivert": node.aktivert,
                "tilkobla": status.get("tilkobla", False),
                "feil": status.get("feil"),
                "sist_sett": status.get("sist_sett"),
                "tilkobla_sidan": status.get("tilkobla_sidan"),
                "antal_kanalar": status.get("antal_kanalar", 0),
            })

        totalt_kanalar = sum(n.get("antal_kanalar", 0) for n in nodar_info)
        tilkobla_antal = sum(1 for n in nodar_info if n.get("tilkobla"))

    return {
        "modus": "hub",
        "startet": _hub_startet,
        "totalt_kanalar": totalt_kanalar,
        "totalt_nodar": len(nodar_info),
        "tilkobla_nodar": tilkobla_antal,
        "nodar": nodar_info,
        "ip": os.environ.get("OPENDAQ_IP", _hent_ip()),
    }


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
    global _hub_konfig

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
    for node in ny_konfig.nodar:
        if node.id not in gamle_ids and node.aktivert:
            _koble_til_node(node)

    return True, f"Konfig oppdatert ({len(ny_konfig.nodar)} nodar)"


def legg_til_node_api(data: dict) -> tuple:
    """Legg til ein ny node og koble til. Returns (suksess, melding, node_dict)."""
    import uuid as _uuid

    adresse = str(data.get("adresse", "")).strip()
    if not adresse:
        return False, "Mangler 'adresse'", None

    namn = str(data.get("namn", "")).strip() or adresse
    port = int(data.get("port", 4840))
    protokoll = str(data.get("protokoll", "daq.opcua"))
    lokasjon = str(data.get("lokasjon", ""))

    node = FjernNode(
        id=_uuid.uuid4().hex[:8],
        namn=namn,
        adresse=adresse,
        port=port,
        aktivert=True,
        protokoll=protokoll,
        lokasjon=lokasjon,
    )

    with _hub_lock:
        _hub_konfig.nodar.append(node)
    lagre_hub_konfig(_hub_konfig)

    ok = _koble_til_node(node)
    status_tekst = "tilkobla" if ok else "lagt til (tilkobling feila)"
    return True, f"Node '{namn}' {status_tekst}", node.til_dict()


def fjern_node_api(node_id: str) -> tuple:
    """Fjern ein node. Returns (suksess, melding)."""
    global _hub_konfig

    with _hub_lock:
        node = next((n for n in _hub_konfig.nodar if n.id == node_id), None)
        if not node:
            return False, f"Node '{node_id}' ikkje funnen"
        _hub_konfig.nodar = [n for n in _hub_konfig.nodar if n.id != node_id]

    _fråkoble_node(node_id)
    lagre_hub_konfig(_hub_konfig)
    return True, f"Node '{node.namn}' fjerna"


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
    global _hub_konfig, _hub_startet

    log.info("=" * 60)
    log.info("  openDAQ Hub — Aggregator")
    log.info("=" * 60)

    # Les konfig
    _hub_konfig = les_hub_konfig()
    log.info(f"  Nodar konfigurert: {len(_hub_konfig.nodar)}")
    log.info(f"  Helsesjekk-intervall: {_hub_konfig.helsesjekk_intervall}s")
    log.info(f"  Reconnect-intervall: {_hub_konfig.reconnect_intervall}s")

    # Opprett openDAQ Instance
    _opprett_instance()

    # Koble til aktive nodar
    for node in _hub_konfig.nodar:
        if node.aktivert:
            _koble_til_node(node)
        else:
            log.info(f"  Node '{node.namn}' deaktivert — hoppar over")

    # Oppdater root device kanalar til å matche fjern-nodar.
    # Må skje FØR server-start slik at OPC-UA/NativeStreaming
    # eksponerer rett antal kanalar frå starten.
    _oppdater_root_kanalar()

    # Start serverar
    _start_serverar()

    _hub_startet = datetime.now().isoformat()

    # Start helsesjekk-tråd
    helsesjekk_traad = threading.Thread(target=_helsesjekk_loop, daemon=True)
    helsesjekk_traad.start()

    # Start data-relay tråd (les fjern-nodar → oppdater hub RefDevice DC)
    relay_traad = threading.Thread(target=_data_relay_loop, daemon=True)
    relay_traad.start()
    log.info("Helsesjekk-tråd starta")

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
    flask_app.run(host="0.0.0.0", port=web_port, use_reloader=False)


def main():
    start_hub()


if __name__ == "__main__":
    main()
