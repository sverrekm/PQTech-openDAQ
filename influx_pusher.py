#!/usr/bin/env python3
"""
InfluxDB-skrivar — del kanalverdiar til Grafana via InfluxDB v2
================================================================
Bakgrunnstråd som med jamne mellomrom les kanalverdiane og skriv dei til
InfluxDB v2 (Flux) sitt write-API som measurement `pqtech_channel` med
tags {node, channel, unit} og felt `value`. Grafana grafar dei frå bucketen.

Konfig i /data/konfig/influx.json (eller env: INFLUX_URL/TOKEN/ORG/BUCKET).
Token vert aldri returnert til GUI-et (berre token_satt: bool).
"""

import os
import json
import time
import logging
import threading
import urllib.request
import urllib.parse
from pathlib import Path

log = logging.getLogger("influx_pusher")

KONFIG_STI = Path("/data/konfig/influx.json")

_STANDARD = {
    "aktivert": False,
    "url": "",
    "token": "",
    "org": "",
    "bucket": "",
    "intervall_s": 10,
}


def les_konfig() -> dict:
    d = dict(_STANDARD)
    try:
        if KONFIG_STI.exists():
            d.update(json.loads(KONFIG_STI.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning(f"Kunne ikkje lese influx-konfig: {e}")
    # Env-overstyring (set aktivert viss url+token kjem frå env)
    env_map = {"url": "INFLUX_URL", "token": "INFLUX_TOKEN",
               "org": "INFLUX_ORG", "bucket": "INFLUX_BUCKET"}
    for nokkel, env in env_map.items():
        v = os.environ.get(env, "").strip()
        if v:
            d[nokkel] = v
    if os.environ.get("INFLUX_URL") and os.environ.get("INFLUX_TOKEN"):
        d["aktivert"] = True
    try:
        d["intervall_s"] = max(2, int(d.get("intervall_s", 10)))
    except (TypeError, ValueError):
        d["intervall_s"] = 10
    return d


def lagre_konfig(data: dict) -> dict:
    d = dict(_STANDARD)
    if KONFIG_STI.exists():
        try:
            d.update(json.loads(KONFIG_STI.read_text(encoding="utf-8")))
        except Exception:
            pass
    for nokkel in ("aktivert", "url", "org", "bucket", "intervall_s"):
        if nokkel in data:
            d[nokkel] = data[nokkel]
    # token: utelat => behald, tom streng => fjern
    if "token" in data and data["token"] is not None:
        d["token"] = str(data["token"]).strip()
    KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
    KONFIG_STI.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return d


def konfig_offentleg() -> dict:
    """Konfig for GUI — token maska til token_satt."""
    d = les_konfig()
    return {
        "aktivert": bool(d.get("aktivert")),
        "url": d.get("url", ""),
        "org": d.get("org", ""),
        "bucket": d.get("bucket", ""),
        "intervall_s": d.get("intervall_s", 10),
        "token_satt": bool(d.get("token")),
    }


def _esc_tag(s) -> str:
    return (str(s).replace("\\", "\\\\").replace(" ", "\\ ")
            .replace(",", "\\,").replace("=", "\\="))


def _line_protocol(kanalar) -> str:
    linjer = []
    for k in kanalar:
        verdi = k.get("verdi")
        if verdi is None:
            continue
        try:
            fv = float(verdi)
        except (TypeError, ValueError):
            continue
        node = _esc_tag(k.get("node_namn") or k.get("node_id") or "ukjend")
        ch = _esc_tag(k.get("namn") or "ukjend")
        unit = _esc_tag(k.get("eining") or "")
        linjer.append(f"pqtech_channel,node={node},channel={ch},unit={unit} value={fv}")
    return "\n".join(linjer)


def skriv_ein_gong(hent_kanalar) -> tuple:
    """Skriv kanalverdiar til Influx no. Returnerer (ok, melding)."""
    d = les_konfig()
    if not (d.get("url") and d.get("token")):
        return False, "Influx ikkje konfigurert (manglar url/token)"
    try:
        body = _line_protocol(hent_kanalar()).encode("utf-8")
    except Exception as e:
        return False, f"Kunne ikkje lese kanalar: {e}"
    if not body:
        return True, "Ingen verdiar å skrive"
    qs = urllib.parse.urlencode({
        "org": d.get("org", ""), "bucket": d.get("bucket", ""), "precision": "s"})
    url = f"{d['url'].rstrip('/')}/api/v2/write?{qs}"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Token {d['token']}",
                 "Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204), f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read(200).decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return False, str(e)


def start(hent_kanalar) -> None:
    """Start bakgrunnstråd som skriv til Influx kvart intervall_s sekund."""
    def loop():
        while True:
            d = les_konfig()
            if d.get("aktivert") and d.get("url") and d.get("token"):
                ok, melding = skriv_ein_gong(hent_kanalar)
                if not ok:
                    log.warning(f"Influx-skriving feila: {melding}")
            time.sleep(les_konfig().get("intervall_s", 10))
    threading.Thread(target=loop, daemon=True, name="influx_pusher").start()
    log.info("Influx-skrivar starta")
