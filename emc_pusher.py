#!/usr/bin/env python3
"""
EMC / spektral-analyse → InfluxDB (for Grafana)
================================================
Reknar FFT på rå SIRIUS-ADC-bølgjeformer (20 kHz) og skriv til InfluxDB:
- pqtech_harmonic{node,channel,unit,h}  value = amplitude per harmonisk (H1..Hn)
- pqtech_thd{node,channel}              value = THD i %
- pqtech_fft{node,channel,freq}         value = amplitude per frekvens-bin (spektrogram)

Brukar same InfluxDB-tilkopling som influx_pusher (url/token/org/bucket).
Eigen konfig i /data/konfig/emc.json: aktivert, intervall_s, nettfrekvens,
n_harmoniske, syklusar (FFT-vindauge), fft_maks_hz, fft_bins.

DSP: vindauget er eit heiltal nett-syklusar (rektangulært → minimal lekkasje
for harmoniske som ligg eksakt på bins). amplitude[k] = 2/N * |X[k]|.
THD = sqrt(sum H2..Hn^2) / H1.
"""

import os
import json
import time
import logging
import threading
import urllib.request
import urllib.parse
from pathlib import Path

import numpy as np

import influx_pusher

log = logging.getLogger("emc_pusher")

KONFIG_STI = Path("/data/konfig/emc.json")

_STANDARD = {
    "aktivert": False,
    "intervall_s": 5,
    "nettfrekvens": 50,     # Hz (grunnfrekvens)
    "n_harmoniske": 50,     # H1..Hn
    "syklusar": 10,         # nett-syklusar per FFT-vindauge (oppløysing = f0/syklusar)
    "fft_maks_hz": 2000,    # øvre frekvens for spektrogram
    "fft_bins": 200,        # tal frekvens-bins i spektrogram (nedsampla)
}


def les_konfig() -> dict:
    d = dict(_STANDARD)
    try:
        if KONFIG_STI.exists():
            d.update(json.loads(KONFIG_STI.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning(f"Kunne ikkje lese emc-konfig: {e}")
    try:
        d["intervall_s"] = max(1, int(d.get("intervall_s", 5)))
    except (TypeError, ValueError):
        d["intervall_s"] = 5
    return d


def lagre_konfig(data: dict) -> dict:
    d = les_konfig()
    for nokkel in ("aktivert", "intervall_s", "nettfrekvens", "n_harmoniske",
                   "syklusar", "fft_maks_hz", "fft_bins"):
        if nokkel in data:
            d[nokkel] = data[nokkel]
    KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
    KONFIG_STI.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return d


def konfig_offentleg() -> dict:
    return les_konfig()


def _esc_tag(s) -> str:
    return (str(s).replace("\\", "\\\\").replace(" ", "\\ ")
            .replace(",", "\\,").replace("=", "\\="))


def analyser_kanal(samples: np.ndarray, sr: float, k: dict) -> dict:
    """FFT-analyse av eitt kanal-vindauge. Returnerer harmoniske, THD, spektrum."""
    f0 = float(k.get("nettfrekvens", 50)) or 50.0
    syklusar = max(1, int(k.get("syklusar", 10)))
    n_harm = max(1, int(k.get("n_harmoniske", 50)))

    # Trim til heiltal syklusar
    n_per_syklus = sr / f0
    N = int(round(syklusar * n_per_syklus))
    if N < 8 or len(samples) < N:
        return {}
    x = np.asarray(samples[-N:], dtype=np.float64)
    x = x - x.mean()  # fjern DC

    X = np.fft.rfft(x)
    amp = (2.0 / N) * np.abs(X)          # peak-amplitude per bin
    freqs = np.fft.rfftfreq(N, d=1.0 / sr)

    # Harmoniske: bin for harmonisk h ligg på h*syklusar (sidan N=syklusar*sr/f0)
    harm = {}
    for h in range(1, n_harm + 1):
        bin_idx = h * syklusar
        if bin_idx < len(amp):
            harm[h] = float(amp[bin_idx])
    grunn = harm.get(1, 0.0)
    if grunn > 1e-9:
        rest = np.sqrt(sum(v * v for hh, v in harm.items() if hh >= 2))
        thd = 100.0 * rest / grunn
    else:
        thd = 0.0

    # Spektrum (nedsampla til fft_bins opp til fft_maks_hz) for spektrogram
    maks_hz = float(k.get("fft_maks_hz", 2000))
    n_bins = max(8, int(k.get("fft_bins", 200)))
    maske = freqs <= maks_hz
    f_sel = freqs[maske]
    a_sel = amp[maske]
    spektrum = []
    if len(f_sel) > 1:
        kant = np.linspace(f_sel[0], f_sel[-1], n_bins + 1)
        idx = np.digitize(f_sel, kant) - 1
        for b in range(n_bins):
            sel = a_sel[idx == b]
            if sel.size:
                spektrum.append((float((kant[b] + kant[b + 1]) / 2.0), float(sel.max())))
    return {"harmoniske": harm, "thd": thd, "spektrum": spektrum}


def skriv_ein_gong(hent_vindu) -> tuple:
    """hent_vindu() -> (samples (N,8) float64, sample_rate, klar, {idx:(namn,eining)})
    eller None. Reknar FFT per aktiv kanal og skriv til Influx."""
    konf = les_konfig()
    inf = influx_pusher.les_konfig()
    if not (inf.get("url") and inf.get("token")):
        return False, "InfluxDB ikkje konfigurert (sjå Share to Grafana)"
    try:
        res = hent_vindu()
    except Exception as e:
        return False, f"Kunne ikkje hente bølgjeform: {e}"
    if not res:
        return False, "Ingen rå ADC-data (SIRIUS ikkje aktiv?)"
    samples, sr, klar, kanalar = res
    if not klar:
        return False, "Skalering ikkje klar enno (vent på nullpunkt-kalibrering)"
    nodenamn = os.environ.get("HOSTNAME") or "node"
    try:
        import socket
        nodenamn = socket.gethostname()
    except Exception:
        pass

    linjer = []
    for idx, (namn, eining) in kanalar.items():
        if idx >= samples.shape[1]:
            continue
        r = analyser_kanal(samples[:, idx], sr, {**konf})
        if not r:
            continue
        nm = _esc_tag(namn)
        un = _esc_tag(eining or "")
        nd = _esc_tag(nodenamn)
        for h, v in r["harmoniske"].items():
            linjer.append(f"pqtech_harmonic,node={nd},channel={nm},unit={un},h={h} value={v}")
        linjer.append(f"pqtech_thd,node={nd},channel={nm} value={r['thd']}")
        for f_hz, v in r["spektrum"]:
            linjer.append(f"pqtech_fft,node={nd},channel={nm},freq={f_hz:.1f} value={v}")

    if not linjer:
        return True, "Ingen kanalar å analysere"
    return skriv_linjer(linjer)


def skriv_linjer(linjer: list) -> tuple:
    """Skriv ferdige line-protocol-linjer til InfluxDB. Delt av node- og
    hub-side EMC (hub_emc). Returnerer (ok, melding)."""
    if not linjer:
        return True, "Ingen punkt å skrive"
    inf = influx_pusher.les_konfig()
    if not (inf.get("url") and inf.get("token")):
        return False, "InfluxDB ikkje konfigurert (sjå Share to Grafana)"
    body = "\n".join(linjer).encode("utf-8")
    qs = urllib.parse.urlencode({"org": inf.get("org", ""),
                                 "bucket": inf.get("bucket", ""), "precision": "s"})
    url = f"{inf['url'].rstrip('/')}/api/v2/write?{qs}"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Token {inf['token']}",
                 "Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204), f"HTTP {resp.status} ({len(linjer)} punkt)"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read(200).decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return False, str(e)


def start(hent_vindu) -> None:
    """Start bakgrunnstråd som reknar + skriv EMC-data kvart intervall_s."""
    def loop():
        while True:
            konf = les_konfig()
            if konf.get("aktivert"):
                ok, melding = skriv_ein_gong(hent_vindu)
                if not ok:
                    log.warning(f"EMC-skriving feila: {melding}")
            time.sleep(les_konfig().get("intervall_s", 5))
    threading.Thread(target=loop, daemon=True, name="emc_pusher").start()
    log.info("EMC-skrivar starta")
