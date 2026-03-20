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
_instance = None                # openDAQ Instance
_hub_konfig: HubKonfig = HubKonfig()
_node_devices = {}              # node_id -> openDAQ device-objekt
_node_status = {}               # node_id -> {"tilkobla": bool, "feil": str, ...}
_helsesjekk_aktiv = True
_hub_startet = None             # ISO timestamp
_hub_buffer: HubBuffer = None   # Hub-side buffer sync


def hent_logg(antall=200):
    """Returner dei siste N logg-linjene."""
    return _logg_buffer.hent_linjer(min(antall, 500))


# --- openDAQ Instance og tilkoblingar ---

def _opprett_instance():
    """Opprett openDAQ Instance med root device for DeviceInfo.

    set_root_device("daqref://device0") gjer at C++ patchen i Dockerfile
    set DeviceInfo (serial, model, manufacturer, MAC, DeviceType).
    Utan dette ser DewesoftX ein eining utan serienummer → "disconnected".
    NumberOfChannels=0 hindrar dummy-kanalar frå ref-devicen — dei ekte
    kanalane kjem frå fjern-nodar via add_device().
    """
    global _instance

    # CWD må vere /usr/local/lib for at ModuleManager skal finne .module.so
    module_path = os.environ.get("OPENDAQ_MODULE_PATH", "/usr/local/lib")

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
    log.info("openDAQ Instance oppretta (hub-modus, med root device)")

    # Sett NumberOfChannels til 1 (minimum krav for ref device).
    # 0 kanalar kan gjere at DewesoftX avviser eininga som ugyldig.
    # Dummy-kanalen frå ref-devicen er akseptabel overhead.
    try:
        _instance.set_property_value("NumberOfChannels", 1)
        log.info("  Root device: NumberOfChannels sett til 1")
    except Exception as e:
        log.warning(f"  Kunne ikkje sette NumberOfChannels=1: {e}")

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


def _koble_til_node(node: FjernNode) -> bool:
    """Prøv å koble til ein fjern-node via add_device().

    OPC-UA-serverar annonserer endpoint-URL med si eiga IP (t.d. macvlan).
    Når hub-en koplar via Tailscale-IP, feilar OPC-UA-klienten fordi den
    prøver å rekoble til serveren si annonserte IP. Vi brukar device-config
    for å overstyre dette.
    """
    global _instance, _node_devices, _node_status

    import opendaq as daq

    tilkobling = node.tilkobling_streng
    log.info(f"Koplar til node '{node.namn}' ({tilkobling})...")

    try:
        # Prøv å hente device-config for å overstyre endpoint-URL
        config = None
        try:
            dev_types = _instance.available_device_types
            # Prøv OPC-UA config-type
            for type_id in dev_types:
                if 'opcua' in type_id.lower() or 'daq.opcua' in type_id.lower():
                    config = dev_types[type_id].create_default_config()
                    # Logg tilgjengelege eigenskapar
                    for p in config.visible_properties:
                        try:
                            v = config.get_property_value(p.name)
                            log.info(f"  DeviceConfig [{type_id}] {p.name} = {v!r}")
                        except Exception:
                            pass
                    break
        except Exception as e:
            log.info(f"  Ingen device-config tilgjengeleg: {e}")

        device = _instance.add_device(tilkobling, config)
        with _hub_lock:
            _node_devices[node.id] = device
            _node_status[node.id] = {
                "tilkobla": True,
                "feil": None,
                "sist_sett": datetime.now().isoformat(),
                "tilkobla_sidan": datetime.now().isoformat(),
                "antal_kanalar": _tel_kanalar(device),
            }
        log.info(f"  Tilkobla: '{node.namn}' — "
                 f"{_node_status[node.id]['antal_kanalar']} kanalar")
        return True
    except Exception as e:
        with _hub_lock:
            _node_devices.pop(node.id, None)
            _node_status[node.id] = {
                "tilkobla": False,
                "feil": str(e),
                "sist_sett": None,
                "tilkobla_sidan": None,
                "antal_kanalar": 0,
            }
        log.warning(f"  Feil ved tilkobling til '{node.namn}': {e}")
        return False


def _fråkoble_node(node_id: str):
    """Fråkoble og fjern ein node frå instansen."""
    global _instance, _node_devices, _node_status

    with _hub_lock:
        device = _node_devices.pop(node_id, None)
        _node_status.pop(node_id, None)

    if device and _instance:
        try:
            _instance.remove_device(device)
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

            # Hopp over NativeConfiguration — kan feile mellom versjonar
            if proto_id == "OpenDAQNativeConfiguration":
                log.info(f"  Cap {proto_id}: HOPPA OVER (daq.nd:// deaktivert)")
                continue

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

    log.info("  Nil string-eigenskapar fiksa")


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

    n_kanalar = 1  # Hub root device har 1 dummy-kanal
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
                    # Prøv remove_device
                    try:
                        _instance.remove_device(device)
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

    # Start serverar
    _start_serverar()

    _hub_startet = datetime.now().isoformat()

    # Start helsesjekk-tråd
    helsesjekk_traad = threading.Thread(target=_helsesjekk_loop, daemon=True)
    helsesjekk_traad.start()
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
