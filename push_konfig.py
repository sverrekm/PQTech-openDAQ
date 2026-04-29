#!/usr/bin/env python3
"""
Push-konfigurasjon — node/sub-hub som sender data oppover til parent
=====================================================================
Når `parent_url` er sett, startar `hub_pusher`-tråden som POST-ar
JSON-batchar med kanalverdiar til {parent_url}/api/ingest kvart
1/push_hz sekund.

Persistert til /data/konfig/push.json.

Bruk:
    from push_konfig import les_push_konfig, lagre_push_konfig
"""

import json
import logging
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

log = logging.getLogger('push_konfig')

PUSH_KONFIG_STI = Path("/data/konfig/push.json")


@dataclass
class PushKonfig:
    """Konfigurasjon for å sende data til ein 'parent' hub."""
    parent_url: str = ""              # T.d. "https://opendac.pqtech.no". Tom = ingen push.
    parent_token: str = ""            # Bearer-token for autentisering mot parent
    node_id: str = ""                 # Globalt unik (auto-generert hex hvis tom)
    node_namn: str = ""               # Display-namn (t.d. "Sundet PQube")
    push_hz: float = 10.0             # Send-rate i Hz (1-50)
    accept_ingest: bool = False       # True = denne konteinaren mottek òg push (sub-hub/sentral)
    ingest_token: str = ""            # Token denne konteinaren krev av sine eigne barn
    # Verdi-type: kva felt frå opendaq_bro._siste_verdiar som vert sendt.
    # 'siste' = siste sample (god for DC, temp, low-rate)
    # 'rms'   = RMS av siste pakke (god for AC-spenning, AC-straum)
    # 'snitt' = gjennomsnitt
    verdi_type: str = "siste"

    def __post_init__(self):
        if not self.node_id:
            self.node_id = uuid.uuid4().hex[:12]


def les_push_konfig() -> PushKonfig:
    """Les push-konfig frå JSON-fil. Returnerer standard viss fila ikkje finst."""
    try:
        if PUSH_KONFIG_STI.exists():
            data = json.loads(PUSH_KONFIG_STI.read_text(encoding='utf-8'))
            konfig = PushKonfig(
                parent_url=str(data.get("parent_url", "")).strip(),
                parent_token=str(data.get("parent_token", "")).strip(),
                node_id=str(data.get("node_id", "")).strip(),
                node_namn=str(data.get("node_namn", "")).strip(),
                push_hz=float(data.get("push_hz", 10.0)),
                accept_ingest=bool(data.get("accept_ingest", False)),
                ingest_token=str(data.get("ingest_token", "")).strip(),
                verdi_type=str(data.get("verdi_type", "siste")),
            )
            log.info(f"Lasta push-konfig: parent={konfig.parent_url or '(ingen)'}, "
                     f"node_id={konfig.node_id}, push_hz={konfig.push_hz}, "
                     f"accept_ingest={konfig.accept_ingest}")
            # Lagre tilbake hvis node_id vart auto-generert
            if not data.get("node_id"):
                lagre_push_konfig(konfig)
            return konfig
    except Exception as e:
        log.warning(f"Kunne ikkje lese push-konfig: {e}")
    konfig = PushKonfig()
    # Persistér default ved fyrste oppstart slik at node_id er stabil
    try:
        lagre_push_konfig(konfig)
    except Exception:
        pass
    return konfig


def lagre_push_konfig(konfig: PushKonfig) -> bool:
    """Lagre push-konfig til JSON-fil."""
    try:
        PUSH_KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        PUSH_KONFIG_STI.write_text(
            json.dumps(asdict(konfig), indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        log.info(f"Lagra push-konfig: parent={konfig.parent_url or '(ingen)'}")
        return True
    except Exception as e:
        log.error(f"Kunne ikkje lagre push-konfig: {e}")
        return False


def valider_push_konfig(data: dict) -> tuple:
    """Valider push-konfig frå API-input.

    Returns:
        (PushKonfig, feilmelding) - konfig er None viss validering feilar
    """
    if not isinstance(data, dict):
        return None, "Forventa eit objekt"

    parent_url = str(data.get("parent_url", "")).strip()
    if parent_url and not (parent_url.startswith("http://") or parent_url.startswith("https://")):
        return None, "parent_url maa starte med http:// eller https://"

    try:
        push_hz = float(data.get("push_hz", 10.0))
    except (TypeError, ValueError):
        return None, "push_hz maa vere eit tal"
    if push_hz < 0.1 or push_hz > 100.0:
        return None, f"push_hz {push_hz} utanfor 0.1-100"

    node_id = str(data.get("node_id", "")).strip()
    node_namn = str(data.get("node_namn", "")).strip()
    parent_token = str(data.get("parent_token", "")).strip()
    accept_ingest = bool(data.get("accept_ingest", False))
    ingest_token = str(data.get("ingest_token", "")).strip()

    konfig = PushKonfig(
        parent_url=parent_url,
        parent_token=parent_token,
        node_id=node_id or uuid.uuid4().hex[:12],
        node_namn=node_namn,
        push_hz=push_hz,
        accept_ingest=accept_ingest,
        ingest_token=ingest_token,
    )
    return konfig, None
