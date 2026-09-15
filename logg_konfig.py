#!/usr/bin/env python3
"""
Per-node logge-styring på hubben
================================
Styrer om måledata som ein node pushar faktisk vert **lagra** på hubben
(hub_lager SQLite + NAS rå-fil-arkiv). Live-visninga på dashbordet er ikkje
råka — du ser framleis at noden lever, men det vert ikkje skrive til disk.

Nyttig når ein node skal stengast ned eller flyttast (unngå å fylle lageret
med unødvendig data), eller når du berre vil logge i eit bestemt tidsvindu.

Modus per node:
  - 'kontinuerleg' : lagre alltid (standard)
  - 'av'           : lagre aldri (pause)
  - 'planlagt'     : lagre berre mellom start_ms og slutt_ms (epoch ms; 0 = ope)

Persistert til /data/konfig/logging.json. Nøkkel = node-namn (same match-nøkkel
som injiser_push_verdiar brukar), med node_id som reserve-alias.

Bruk:
    from logg_konfig import logging_aktiv, les_logg_konfig, sett_node_logg
"""

import json
import time
import logging
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path

log = logging.getLogger('logg_konfig')

LOGG_KONFIG_STI = Path("/data/konfig/logging.json")

MODUS_KONTINUERLEG = "kontinuerleg"
MODUS_AV = "av"
MODUS_PLANLAGT = "planlagt"
GYLDIGE_MODUS = {MODUS_KONTINUERLEG, MODUS_AV, MODUS_PLANLAGT}

# mtime-cache: logging_aktiv vert kalla på kvar /api/ingest (10+ Hz per node),
# so vi re-les berre fila når ho endrar seg.
_cache_lock = threading.Lock()
_cache: dict = None
_cache_mtime = None


@dataclass
class NodeLogg:
    """Logge-innstilling for éin node."""
    modus: str = MODUS_KONTINUERLEG
    start_ms: int = 0     # epoch ms; 0 = ope (ingen startgrense)
    slutt_ms: int = 0     # epoch ms; 0 = ope (ingen sluttgrense)

    def til_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fraa_dict(cls, d: dict) -> "NodeLogg":
        modus = str(d.get("modus", MODUS_KONTINUERLEG)).strip()
        if modus not in GYLDIGE_MODUS:
            modus = MODUS_KONTINUERLEG
        try:
            start_ms = int(d.get("start_ms", 0) or 0)
        except (TypeError, ValueError):
            start_ms = 0
        try:
            slutt_ms = int(d.get("slutt_ms", 0) or 0)
        except (TypeError, ValueError):
            slutt_ms = 0
        return cls(modus=modus, start_ms=start_ms, slutt_ms=slutt_ms)


@dataclass
class LoggKonfig:
    """Alle per-node logge-innstillingar. Nøkkel = node-namn."""
    innstillingar: dict = field(default_factory=dict)  # {node_namn: NodeLogg}

    def til_dict(self) -> dict:
        return {"innstillingar": {k: v.til_dict() for k, v in self.innstillingar.items()}}

    @classmethod
    def fraa_dict(cls, d: dict) -> "LoggKonfig":
        inn = {}
        for k, v in (d.get("innstillingar", {}) or {}).items():
            if isinstance(v, dict):
                inn[str(k)] = NodeLogg.fraa_dict(v)
        return cls(innstillingar=inn)


def les_logg_konfig() -> LoggKonfig:
    """Les logge-konfig (mtime-cacha)."""
    global _cache, _cache_mtime
    try:
        mtime = LOGG_KONFIG_STI.stat().st_mtime if LOGG_KONFIG_STI.exists() else None
    except OSError:
        mtime = None
    with _cache_lock:
        if _cache is not None and mtime == _cache_mtime:
            return _cache
    konfig = LoggKonfig()
    try:
        if LOGG_KONFIG_STI.exists():
            data = json.loads(LOGG_KONFIG_STI.read_text(encoding="utf-8"))
            konfig = LoggKonfig.fraa_dict(data)
    except Exception as e:
        log.warning(f"Kunne ikkje lese logg-konfig: {e}")
    with _cache_lock:
        _cache = konfig
        _cache_mtime = mtime
    return konfig


def lagre_logg_konfig(konfig: LoggKonfig) -> bool:
    """Lagre logge-konfig til disk."""
    global _cache, _cache_mtime
    try:
        LOGG_KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        LOGG_KONFIG_STI.write_text(
            json.dumps(konfig.til_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8")
    except Exception as e:
        log.error(f"Kunne ikkje lagre logg-konfig: {e}")
        return False
    with _cache_lock:
        _cache = konfig
        try:
            _cache_mtime = LOGG_KONFIG_STI.stat().st_mtime
        except OSError:
            _cache_mtime = None
    return True


def _finn(konfig: LoggKonfig, node_namn: str, node_id: str) -> NodeLogg:
    inn = konfig.innstillingar
    if node_namn and node_namn in inn:
        return inn[node_namn]
    if node_id and node_id in inn:
        return inn[node_id]
    return NodeLogg()  # standard: kontinuerleg


def logging_aktiv(node_id: str = "", node_namn: str = "",
                  no_ms: float = None) -> bool:
    """True viss data frå denne noden skal lagrast akkurat no.

    Ukjend node (ingen innstilling) => True (logg som standard).
    """
    konfig = les_logg_konfig()
    ni = _finn(konfig, node_namn or "", node_id or "")
    if ni.modus == MODUS_KONTINUERLEG:
        return True
    if ni.modus == MODUS_AV:
        return False
    # planlagt
    no = no_ms if no_ms is not None else time.time() * 1000.0
    if ni.start_ms and no < ni.start_ms:
        return False
    if ni.slutt_ms and no > ni.slutt_ms:
        return False
    return True


def sett_node_logg(node_namn: str, modus: str,
                   start_ms: int = 0, slutt_ms: int = 0) -> tuple:
    """Set logge-innstilling for éin node. (ok, melding)."""
    node_namn = str(node_namn or "").strip()
    if not node_namn:
        return False, "Manglar node-namn"
    modus = str(modus or "").strip()
    if modus not in GYLDIGE_MODUS:
        return False, f"Ugyldig modus '{modus}'"
    if modus == MODUS_PLANLAGT and start_ms and slutt_ms and slutt_ms <= start_ms:
        return False, "Slutt-tid må vere etter start-tid"
    konfig = les_logg_konfig()
    konfig.innstillingar[node_namn] = NodeLogg(
        modus=modus, start_ms=int(start_ms or 0), slutt_ms=int(slutt_ms or 0))
    if lagre_logg_konfig(konfig):
        return True, "Logge-innstilling lagra"
    return False, "Lagring feila"
