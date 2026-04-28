#!/usr/bin/env python3
"""
Hub-pusher — sender kanalverdiar til parent hub via HTTPS POST
================================================================
Bakgrunnstråd som kvart 1/push_hz sek les siste kanal-verdiar frå
opendaq-broen og sender ein JSON-batch til {parent_url}/api/ingest.

Bruksmåte:
    from hub_pusher import HubPusher
    pusher = HubPusher(hent_verdiar_fn=opendaq_bro.hent_siste_verdiar)
    pusher.start()
    ...
    pusher.stopp()

Payload-format:
    {
      "node_id": "ab12cd34ef56",
      "node_namn": "Sundet PQube",
      "ts": 1714230000.123,
      "kanalar": {"AI 0": 230.5, "V_L1_N": 232.1},
      "buffer_lag_ms": 0
    }
"""

import json
import time
import logging
import threading
from typing import Callable, Optional

try:
    import requests
except ImportError:
    requests = None

from push_konfig import PushKonfig, les_push_konfig

log = logging.getLogger('hub_pusher')


class HubPusher:
    """Push-tråd som sender kanalverdiar til parent hub.

    Args:
        hent_verdiar_fn: callable som returnerer dict {kanal_namn: verdi}
        konfig: PushKonfig (les frå disk hvis None)
    """

    def __init__(self, hent_verdiar_fn: Callable[[], dict],
                 konfig: Optional[PushKonfig] = None):
        self._hent_verdiar = hent_verdiar_fn
        self._konfig = konfig if konfig is not None else les_push_konfig()
        self._stopp = threading.Event()
        self._traad: Optional[threading.Thread] = None
        # Statistikk for status-API
        self._sendt_ok = 0
        self._sendt_feil = 0
        self._siste_status_kode: Optional[int] = None
        self._siste_feilmelding: str = ""
        self._siste_send_ts: float = 0.0
        self._siste_latens_ms: float = 0.0

    @property
    def aktiv(self) -> bool:
        """True hvis pusher har gyldig konfig og tråden køyrer."""
        return (bool(self._konfig.parent_url)
                and self._traad is not None
                and self._traad.is_alive())

    def status(self) -> dict:
        """Returner statistikk for web UI."""
        return {
            "konfigurert": bool(self._konfig.parent_url),
            "kjorer": self._traad is not None and self._traad.is_alive(),
            "parent_url": self._konfig.parent_url,
            "node_id": self._konfig.node_id,
            "node_namn": self._konfig.node_namn,
            "push_hz": self._konfig.push_hz,
            "sendt_ok": self._sendt_ok,
            "sendt_feil": self._sendt_feil,
            "siste_status_kode": self._siste_status_kode,
            "siste_feilmelding": self._siste_feilmelding,
            "siste_send_ts": self._siste_send_ts,
            "siste_latens_ms": round(self._siste_latens_ms, 1),
        }

    def start(self):
        """Start push-tråden hvis parent_url er konfigurert."""
        if requests is None:
            log.error("requests-pakken manglar — kan ikkje pushe. Legg til i Dockerfile/requirements.")
            return False
        if not self._konfig.parent_url:
            log.info("Push-pusher: parent_url ikkje sett — startar ikkje")
            return False
        if self._traad is not None and self._traad.is_alive():
            log.warning("Push-pusher: allereie i gang")
            return True

        self._stopp.clear()
        self._traad = threading.Thread(
            target=self._loop, daemon=True, name="hub_pusher")
        self._traad.start()
        log.info(f"Push-pusher starta: {self._konfig.parent_url}, "
                 f"node_id={self._konfig.node_id}, push_hz={self._konfig.push_hz}")
        return True

    def stopp(self):
        """Stopp push-tråden og vent på at han avsluttar."""
        self._stopp.set()
        if self._traad is not None:
            self._traad.join(timeout=3.0)
        log.info(f"Push-pusher stoppa: ok={self._sendt_ok}, feil={self._sendt_feil}")

    def _loop(self):
        """Hovudløkke: les verdiar, send batch, vent intervall."""
        url = f"{self._konfig.parent_url.rstrip('/')}/api/ingest"
        headers = {"Content-Type": "application/json"}
        if self._konfig.parent_token:
            headers["Authorization"] = f"Bearer {self._konfig.parent_token}"

        sesjon = requests.Session()
        # Backoff ved feil — eksponentiell opp til maks 30 sek
        feilteller = 0
        intervall = max(0.01, 1.0 / max(0.1, self._konfig.push_hz))

        log.info(f"Push-loop: url={url}, intervall={intervall*1000:.0f}ms")

        while not self._stopp.is_set():
            t_start = time.time()
            try:
                verdiar = self._hent_verdiar() or {}
                # Fjern interne debug-felt frå payload
                kanalar = {k: v for k, v in verdiar.items()
                           if not k.startswith("_")
                           and isinstance(v, (int, float))}

                if kanalar:
                    payload = {
                        "node_id": self._konfig.node_id,
                        "node_namn": self._konfig.node_namn,
                        "ts": t_start,
                        "kanalar": kanalar,
                        "buffer_lag_ms": 0,
                    }
                    body = json.dumps(payload).encode("utf-8")

                    t_post = time.time()
                    resp = sesjon.post(
                        url, data=body, headers=headers, timeout=5.0)
                    self._siste_latens_ms = (time.time() - t_post) * 1000.0
                    self._siste_status_kode = resp.status_code
                    self._siste_send_ts = t_start

                    if 200 <= resp.status_code < 300:
                        self._sendt_ok += 1
                        feilteller = 0
                        if self._sendt_ok % 100 == 1:
                            log.info(f"Push OK #{self._sendt_ok}: "
                                     f"{len(kanalar)} kanalar, "
                                     f"{self._siste_latens_ms:.0f}ms")
                    else:
                        self._sendt_feil += 1
                        feilteller += 1
                        self._siste_feilmelding = f"HTTP {resp.status_code}: {resp.text[:120]}"
                        if feilteller <= 3 or feilteller % 50 == 0:
                            log.warning(f"Push feil #{feilteller}: {self._siste_feilmelding}")
                else:
                    # Ingen verdiar enno — vent og prøv igjen
                    pass

            except requests.exceptions.RequestException as e:
                self._sendt_feil += 1
                feilteller += 1
                self._siste_feilmelding = f"Nettfeil: {e}"
                if feilteller <= 3 or feilteller % 50 == 0:
                    log.warning(f"Push nettfeil #{feilteller}: {e}")
            except Exception as e:
                self._sendt_feil += 1
                feilteller += 1
                self._siste_feilmelding = f"Uventet: {e}"
                log.exception(f"Push uventet feil: {e}")

            # Backoff ved feil — held intervall ved suksess
            if feilteller > 0:
                ventetid = min(30.0, intervall * (2 ** min(feilteller, 6)))
            else:
                # Trekk frå tidsbruk for jamn rate
                brukt = time.time() - t_start
                ventetid = max(0.0, intervall - brukt)

            self._stopp.wait(timeout=ventetid)

        try:
            sesjon.close()
        except Exception:
            pass

    def oppdater_konfig(self, ny_konfig: PushKonfig):
        """Bytt konfig og restart tråden hvis nødvendig."""
        gammal_url = self._konfig.parent_url
        self._konfig = ny_konfig
        kjorer = self._traad is not None and self._traad.is_alive()
        if kjorer:
            self.stopp()
        if ny_konfig.parent_url:
            self.start()
        elif gammal_url:
            log.info("Push-pusher: parent_url fjerna, ikkje restartar")
