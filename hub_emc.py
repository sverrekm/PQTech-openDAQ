#!/usr/bin/env python3
"""
Hub-side EMC / spektral-analyse
===============================
Reknar harmoniske/THD/spektrum på hubben frå dei rå bølgjeformene som
fjern-nodane streamar over brua (openDAQ). Hubben les alt full-rate samples
per kanal via StreamReader for å relaye til DewesoftX — denne modulen opnar
*eigne* StreamReader-ar, plukkar eit samanhengande vindauge per kanal, og
gjenbrukar FFT-koden i `emc_pusher` (`analyser_kanal`) + Influx-skrivinga
(`skriv_linjer`).

Skil seg frå node-side `emc_pusher` ved at datakjelda er hub-kanalane
(fleire nodar) i staden for éin lokal SIRIUS. Resultata vert tagga med
node-namn så kvar node skiljast i Grafana.

Krev hub-modus (køyrer i hub_server-prosessen) + at EMC er aktivert i
emc.json + at Share to Grafana (InfluxDB) er sett opp.
"""

import os
import time
import logging
import threading

import numpy as np

import emc_pusher

log = logging.getLogger("hub_emc")

# Dedikerte EMC-lesarar (uavhengige av relay-lesarane i hub_server)
_emc_readers = {}      # (node_id, sig_id) -> daq.StreamReader
_MAKS_BACKLOG = 500_000  # drop lesar viss backlog renn over (treg DERP e.l.)
# Lås: hindrar at bakgrunns-loopen og /api/emc/test les same StreamReader
# samtidig (openDAQ-lesarane er ikkje trådsikre → kan henge).
_lock = threading.Lock()


def _node_namn_map() -> dict:
    """node_id -> visningsnamn (for InfluxDB node-tag)."""
    import hub_server
    m = {}
    try:
        for n in hub_server._hub_konfig.nodar:
            m[n.id] = n.namn or n.id
    except Exception:
        pass
    return m


def samle_og_skriv() -> tuple:
    """Plukk eit vindauge per openDAQ-kanal, rekn EMC, skriv til Influx.
    Returnerer (ok, melding)."""
    import hub_server
    import opendaq as daq

    # Hindra samtidig lesing (bakgrunns-loop vs Test-knapp). Ikkje-blokkerande
    # så Test-kallet returnerer kjapt i staden for å henge (524) viss loopen
    # alt køyrer.
    if not _lock.acquire(blocking=False):
        return True, "Hub-EMC køyrer allereie — hoppar over denne runda"
    try:
        return _samle_og_skriv_intern(daq, hub_server)
    finally:
        _lock.release()


def _samle_og_skriv_intern(daq, hub_server):
    konf = emc_pusher.les_konfig()
    f0 = float(konf.get("nettfrekvens", 50)) or 50.0
    syk = max(1, int(konf.get("syklusar", 10)))
    sr = float(os.environ.get("SAMPLE_RATE", "20000")) or 20000.0
    N = int(round(syk * sr / f0))
    if N < 8:
        N = 8

    nodenamn = _node_namn_map()
    with hub_server._hub_lock:
        devices = dict(hub_server._node_devices)
        info = list(hub_server._fjern_kanal_info)

    linjer = []
    n_anal = 0
    for entry in info:
        if entry.get("kanal_type") != "opendaq":
            continue   # modbus-kanalar har inga bølgjeform
        nid = entry.get("node_id")
        ridx = entry.get("ch_idx")
        if not nid or ridx is None:
            continue
        dev = devices.get(nid)
        if not dev:
            continue
        key = None
        try:
            channels = dev.channels
            if ridx >= len(channels):
                continue
            sigs = channels[ridx].signals
            if not sigs or len(sigs) == 0:
                continue
            sig = sigs[0]
            try:
                sig_id = sig.global_id
            except Exception:
                sig_id = f"{nid}_{ridx}"
            key = (nid, sig_id)

            if key not in _emc_readers:
                _emc_readers[key] = daq.StreamReader(sig)
                continue   # nyoppretta — vent til neste runde på samples

            reader = _emc_readers[key]
            count = reader.available_count
            if count > _MAKS_BACKLOG:
                # Streaming-overload — drop lesar, lazy-recreate neste runde
                _emc_readers.pop(key, None)
                continue
            if count < N:
                continue   # ikkje nok samples til eit vindauge enno
            # Les alle tilgjengelege samples (ikkje-blokkerande — dei finst
            # alt) og bruk dei nyaste N. Unngår read(N) som kan blokkere.
            data = reader.read(count)
            if data is None or len(data) < N:
                continue
            arr = np.asarray(data[-N:], dtype=np.float64)
            r = emc_pusher.analyser_kanal(arr, sr, {**konf})
            if not r:
                continue

            nm = emc_pusher._esc_tag(entry.get("namn") or "?")
            un = emc_pusher._esc_tag(entry.get("eining") or "")
            nd = emc_pusher._esc_tag(nodenamn.get(nid, nid))
            for h, v in r["harmoniske"].items():
                linjer.append(f"pqtech_harmonic,node={nd},channel={nm},unit={un},h={h} value={v}")
            linjer.append(f"pqtech_thd,node={nd},channel={nm} value={r['thd']}")
            for f_hz, v in r["spektrum"]:
                linjer.append(f"pqtech_fft,node={nd},channel={nm},freq={f_hz:.1f} value={v}")
            n_anal += 1
        except Exception as e:
            if key is not None:
                _emc_readers.pop(key, None)   # drop ev. død lesar
            log.debug(f"hub_emc kanal {nid}/{ridx}: {e}")

    if not linjer:
        return True, "Ingen kanalar klare enno (samlar samples frå brua...)"
    ok, melding = emc_pusher.skriv_linjer(linjer)
    if ok:
        melding = f"{melding} — {n_anal} kanalar"
    return ok, melding


def start() -> None:
    """Start bakgrunnstråd som reknar + skriv hub-EMC kvart intervall_s.
    Passiv til EMC er aktivert i emc.json."""
    def loop():
        # Vent litt så hub-server + relay er oppe og readers kan opprettast
        time.sleep(8)
        while True:
            try:
                konf = emc_pusher.les_konfig()
                if konf.get("aktivert"):
                    ok, melding = samle_og_skriv()
                    if not ok:
                        log.warning(f"Hub-EMC skriving feila: {melding}")
            except Exception as e:
                log.warning(f"Hub-EMC loop-feil: {e}")
            time.sleep(emc_pusher.les_konfig().get("intervall_s", 5))
    threading.Thread(target=loop, daemon=True, name="hub_emc").start()
    log.info("Hub-EMC starta")
