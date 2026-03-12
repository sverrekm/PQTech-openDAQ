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


def hent_logg(antall=200):
    """Returner dei siste N logg-linjene."""
    return _logg_buffer.hent_linjer(min(antall, 500))


# --- openDAQ Instance og tilkoblingar ---

def _opprett_instance():
    """Opprett openDAQ Instance med klient+server-modular aktive."""
    global _instance

    # CWD må vere /usr/local/lib for at ModuleManager skal finne .module.so
    module_path = os.environ.get("OPENDAQ_MODULE_PATH", "/usr/local/lib")

    import opendaq as daq
    builder = daq.InstanceBuilder()
    builder.add_module_path(module_path)
    builder.add_discovery_server("mdns")

    _instance = builder.build()
    log.info("openDAQ Instance oppretta (hub-modus)")


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

    for node_id, device in nodar_snapshot:
        node_info = konfig_nodar.get(node_id)
        node_namn = node_info.namn if node_info else node_id

        try:
            channels = device.channels
        except Exception as e:
            log.debug(f"hent_hub_kanalar: Kan ikkje lese channels frå {node_id}: {e}")
            continue

        try:
            ch_count = len(channels)
        except Exception:
            ch_count = 0

        if ch_count == 0:
            continue

        log.debug(f"hent_hub_kanalar: Node '{node_namn}' har {ch_count} kanalar")

        for idx in range(ch_count):
            try:
                ch = channels[idx]
            except Exception as e:
                log.debug(f"hent_hub_kanalar: Kan ikkje hente kanal [{idx}] frå {node_id}: {e}")
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
                log.debug(f"hent_hub_kanalar: Kanal '{ch_namn}' har {sig_count} signal")

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
                            log.debug(f"hent_hub_kanalar: Oppretta StreamReader for '{ch_namn}'")
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
                            else:
                                log.debug(f"hent_hub_kanalar: '{ch_namn}' available_count=0")
                        except Exception as e:
                            log.warning(f"hent_hub_kanalar: Lesefeil for '{ch_namn}': {e}")
                            _kanal_readers.pop(reader_key, None)
            except Exception as e:
                log.debug(f"hent_hub_kanalar: Signal-feil for '{ch_namn}': {e}")

            kanalar.append({
                "node_id": node_id,
                "node_namn": node_namn,
                "namn": ch_namn,
                "verdi": verdi,
                "eining": eining,
            })

    return kanalar


def _start_serverar():
    """Start OPC-UA + NativeStreaming serverar på hub-instansen."""
    global _instance

    ip = os.environ.get("OPENDAQ_IP", "")
    servere = []

    for srv_type in ['OpenDAQNativeStreaming', 'OpenDAQOPCUA']:
        try:
            config = None
            try:
                srv_type_obj = _instance.available_server_types.get(srv_type)
                if srv_type_obj:
                    config = srv_type_obj.create_default_config()
            except Exception:
                config = None

            _instance.add_server(srv_type, config)
            servere.append(srv_type)
            log.info(f"  Server starta: {srv_type}")
        except Exception as e:
            log.warning(f"  Server {srv_type} feilet: {e}")

    log.info(f"Hub-serverar aktive: {servere}")
    return servere


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

    # Start web UI
    web_port = int(os.environ.get("WEB_PORT", 8080))
    log.info(f"Startar web UI på port {web_port}...")

    from web_ui import app as flask_app
    flask_app.run(host="0.0.0.0", port=web_port, use_reloader=False)


def main():
    start_hub()


if __name__ == "__main__":
    main()
