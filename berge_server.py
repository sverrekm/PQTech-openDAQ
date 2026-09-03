"""Berge-server — reserveveg inn til ein node naar web-UI-et er nede.

Bakgrunn: web_ui (Flask) koeyrer i ein daemon-traad i node-prosessen. Doeyr
den traaden — typisk ein ImportError etter ei halvferdig oppdatering — held
openDAQ fram med aa svare paa 4840/7420 medan 8080 nektar tilkobling. Noden
ser levande ut for hubben, men er utan GUI. Verre: /api/system/oppdater ligg
paa same doede port, saa noden kan ikkje eingong fjern-oppdaterast ut av
feilen. Einaste vegen inn er fysisk eller SSH.

Denne serveren er forsikringa mot det:

  * BERRE standardbiblioteket, og ingen importar frae appen paa modulnivaa,
    saa han kan ikkje ta same importfeilen som drap web_ui
  * eigen port (BERGE_PORT, standard 8081) — uavhengig av Flask
  * les flaate-tokenet direkte frae JSON-fila, ikkje via push_konfig
  * held auge med web-porten og hugsar siste traceback frae web-traaden
  * kan trigge oppdatering og restart, saa ein knekt node kan reparerast
    over nettet i staden for med bil og skrujarn

Hubben proxar denne paa /node-berge/<node_id>/ (sjaa web_ui.py), slik at
web-GUI-et alltid er naabart via hubben — anten det verkelege UI-et, eller
denne bergesida med traceback og ein restart-knapp.
"""

import json
import os
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PUSH_KONFIG_FIL = "/data/konfig/push.json"

# --- Delt tilstand, fylt av node-prosessen ---------------------------------

_start_tid = time.time()
_web_port = 8080
_web_open = False
_web_sist_sjekka = 0.0
_web_feil = ""          # siste traceback frae web-traaden
_web_feil_tid = 0.0
_web_forsoek = 0
_logg_hentar = None     # callable(antall) -> list[str]
_lock = threading.Lock()


def meld_web_feil(tb: str) -> None:
    """Kallast av web-traaden sin retry-loop naar Flask ikkje vil starte."""
    global _web_feil, _web_feil_tid, _web_forsoek
    with _lock:
        _web_feil = str(tb or "")
        _web_feil_tid = time.time()
        _web_forsoek += 1


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _vakt_loop() -> None:
    """Sjekkar web-porten jamt slik at /berge/status er fersk utan aa
    maatte opne ein socket per foresporsel."""
    global _web_open, _web_sist_sjekka
    while True:
        try:
            open_no = _port_open(_web_port)
            with _lock:
                _web_open = open_no
                _web_sist_sjekka = time.time()
        except Exception:
            pass
        time.sleep(20)


# --- Autorisering ----------------------------------------------------------

def _flaate_token() -> str:
    """Flaate-token lese direkte frae fila.

    Med vilje IKKJE via push_konfig: heile poenget med denne modulen er aa
    fungere naar appen sine eigne importar er knekte.
    """
    try:
        with open(PUSH_KONFIG_FIL, "r", encoding="utf-8") as f:
            data = json.load(f)
        for felt in ("parent_token", "ingest_token"):
            verdi = str(data.get(felt, "") or "").strip()
            if verdi:
                return verdi
    except Exception:
        pass
    return os.environ.get("INGEST_TOKEN", "").strip()


def _privat_klient(ip: str) -> bool:
    """Loopback, RFC1918 eller Tailscale (100.64/10) — altsaa ikkje internett."""
    if ip.startswith("127.") or ip == "::1":
        return True
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            if 16 <= int(ip.split(".")[1]) <= 31:
                return True
        except Exception:
            return False
    if ip.startswith("100."):
        try:
            if 64 <= int(ip.split(".")[1]) <= 127:
                return True
        except Exception:
            return False
    return False


# --- HTML ------------------------------------------------------------------

_SIDE = """<!DOCTYPE html><html lang="no"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Berging &mdash; {namn}</title><style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#f7f7f8;color:#24242a;
margin:0;padding:32px 16px}}
.k{{max-width:760px;margin:0 auto;background:#fff;border:1px solid #e6e6e9;
border-radius:12px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
h1{{font-size:19px;margin:0 0 4px}}
.sub{{font-size:13px;color:#77777f;margin:0 0 22px}}
.rad{{display:flex;gap:10px;font-size:13px;padding:7px 0;border-bottom:1px solid #f0f0f2}}
.rad b{{width:150px;color:#55555d;font-weight:500;flex:none}}
.ned{{color:#c2410c;font-weight:600}}.oppe{{color:#15803d;font-weight:600}}
pre{{background:#faf9f8;border:1px solid #eee;border-radius:8px;padding:14px;
font-size:12px;line-height:1.45;overflow-x:auto;white-space:pre-wrap;
color:#4a4a52;margin:14px 0 0}}
.knappar{{margin-top:22px;display:flex;gap:10px;flex-wrap:wrap}}
button{{font:inherit;font-size:13px;padding:9px 16px;border-radius:8px;
border:1px solid #d8d8dc;background:#fff;cursor:pointer;color:#24242a}}
button:hover{{background:#faf9f8}}
button.p{{background:#D76428;border-color:#D76428;color:#fff}}
button.p:hover{{filter:brightness(1.07)}}
h2{{font-size:14px;margin:26px 0 0;color:#55555d}}
#svar{{margin-top:14px;font-size:13px;color:#55555d}}
</style></head><body><div class="k">
<h1>{namn} &mdash; web-UI-et er nede</h1>
<p class="sub">openDAQ svarar, men Flask-traaden paa port {web_port} gjer det ikkje.
Denne sida kjem frae berge-serveren, som koeyrer utanfor Flask.</p>
<div class="rad"><b>Web-UI (port {web_port})</b><span class="{klasse}">{status}</span></div>
<div class="rad"><b>Oppetid prosess</b><span>{oppetid}</span></div>
<div class="rad"><b>Startforsoek</b><span>{forsoek}</span></div>
<div class="rad"><b>Versjon</b><span>{versjon}</span></div>
<h2>Siste feil frae web-traaden</h2>
<pre>{feil}</pre>
<div class="knappar">
  <button class="p" onclick="k('oppdater','Hentar ny kode og restartar &mdash; noden er nede i ca. 40 s.')">Oppdater og restart</button>
  <button onclick="k('restart','Restartar prosessen &mdash; noden er nede i ca. 20 s.')">Berre restart</button>
  <button onclick="location.reload()">Oppdater sida</button>
</div>
<div id="svar"></div>
<script>
function k(sti, melding) {{
  var s = document.getElementById('svar');
  s.textContent = melding;
  fetch('berge/' + sti, {{method: 'POST'}})
    .then(function(r) {{ return r.text(); }})
    .then(function(t) {{ s.textContent = melding + ' (' + t.slice(0, 200) + ')'; }})
    .catch(function() {{ s.textContent = melding + ' Kontakten er broten, som venta. Prov aa laste sida om eit halvt minutt.'; }});
}}
</script>
</div></body></html>"""


def _oppetid_tekst(sekund: float) -> str:
    s = int(sekund)
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} t {(s % 3600) // 60} min"
    return f"{s // 86400} d {(s % 86400) // 3600} t"


def _versjon() -> str:
    for sti in ("/app/.versjon", "/data/konfig/.versjon"):
        try:
            with open(sti, "r", encoding="utf-8") as f:
                return f.read().strip()[:40] or "ukjend"
        except Exception:
            continue
    return "ukjend"


# --- HTTP-handtering -------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "PQTechBerge/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # ingen stoey i node-loggen

    # -- hjelparar --

    def _autorisert(self) -> bool:
        tok = _flaate_token()
        gitt = (self.headers.get("X-Hub-Auth", "") or "").strip()
        if not gitt:
            auth = (self.headers.get("Authorization", "") or "").strip()
            if auth.startswith("Bearer "):
                gitt = auth[7:].strip()
        if tok and gitt and gitt == tok:
            return True
        # Utan konfigurert token finst det ingenting aa samanlikne med. Slepp
        # inn frae privat nett / Tailscale, men aldri frae internett.
        if not tok:
            return _privat_klient(self.client_address[0])
        return False

    def _svar(self, kode: int, kropp: bytes, mime: str = "text/plain; charset=utf-8"):
        self.send_response(kode)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(kropp)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(kropp)
        except Exception:
            pass

    def _json(self, kode: int, data: dict):
        self._svar(kode, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _status_data(self) -> dict:
        with _lock:
            open_no, sjekka = _web_open, _web_sist_sjekka
            feil, feil_tid, forsoek = _web_feil, _web_feil_tid, _web_forsoek
        # Fersk sjekk om vakt-loopen ikkje har koeyrt enno
        if sjekka == 0:
            open_no = _port_open(_web_port)
        return {
            "berge": True,
            "web_port": _web_port,
            "web_ui_oppe": open_no,
            "web_sist_sjekka": round(time.time() - sjekka, 1) if sjekka else None,
            "web_startforsoek": forsoek,
            "siste_feil": feil[-4000:],
            "siste_feil_alder_s": round(time.time() - feil_tid, 1) if feil_tid else None,
            "oppetid_s": round(time.time() - _start_tid, 1),
            "pid": os.getpid(),
            "versjon": _versjon(),
        }

    # -- ruter --

    def do_GET(self):
        sti = self.path.split("?")[0].rstrip("/")
        if not self._autorisert():
            return self._json(401, {"feil": "Ikkje autorisert"})

        if sti in ("", "/berge"):
            d = self._status_data()
            namn = os.environ.get("NODE_NAMN", "") or socket.gethostname()
            oppe = d["web_ui_oppe"]
            side = _SIDE.format(
                namn=_html(namn),
                web_port=d["web_port"],
                status="oppe (last sida paa nytt)" if oppe else "nede",
                klasse="oppe" if oppe else "ned",
                oppetid=_oppetid_tekst(d["oppetid_s"]),
                forsoek=d["web_startforsoek"] or "-",
                versjon=_html(d["versjon"]),
                feil=_html(d["siste_feil"] or "Ingen traceback fanga. Traaden kan "
                           "ha doedd foer denne versjonen vart installert, eller "
                           "hengt seg utan aa kaste."),
            )
            return self._svar(200, side.encode("utf-8"), "text/html; charset=utf-8")

        if sti == "/berge/status":
            return self._json(200, self._status_data())

        if sti == "/berge/logg":
            antall = 300
            if "?" in self.path:
                for par in self.path.split("?", 1)[1].split("&"):
                    if par.startswith("n="):
                        try:
                            antall = max(1, min(2000, int(par[2:])))
                        except Exception:
                            pass
            if _logg_hentar is None:
                return self._svar(200, b"(ingen logg-buffer tilgjengeleg)")
            try:
                linjer = _logg_hentar(antall)
            except Exception:
                linjer = ["(logg-hentar feila)", traceback.format_exc()]
            return self._svar(200, ("\n".join(str(x) for x in linjer)).encode("utf-8"))

        return self._json(404, {"feil": "Ukjend berge-sti"})

    def do_POST(self):
        sti = self.path.split("?")[0].rstrip("/")
        if not self._autorisert():
            return self._json(401, {"feil": "Ikkje autorisert"})

        if sti == "/berge/restart":
            self._svar(200, b"Restartar prosessen no")
            threading.Thread(target=_avslutt, args=(1, 0.4), daemon=True).start()
            return

        if sti == "/berge/oppdater":
            # Importer oppdatering LAZY: er den knekt, vil vi vite det i
            # svaret i staden for aa miste heile berge-serveren.
            try:
                import oppdatering
                res = oppdatering.last_ned_og_oppdater()
            except Exception:
                return self._svar(500, ("Oppdatering feila:\n"
                                        + traceback.format_exc()).encode("utf-8"))
            ok = bool(res.get("suksess")) if isinstance(res, dict) else bool(res)
            melding = (res.get("melding", "") if isinstance(res, dict) else "")
            if ok:
                self._svar(200, f"Oppdatert ({melding}) — restartar".encode("utf-8"))
                threading.Thread(target=_avslutt, args=(0, 0.4), daemon=True).start()
            else:
                self._svar(500, f"Oppdatering feila: {melding}".encode("utf-8"))
            return

        return self._json(404, {"feil": "Ukjend berge-sti"})


def _html(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _avslutt(kode: int, forseinking: float) -> None:
    """Gi HTTP-svaret tid til aa naa fram, deretter doe saa containeren
    (restart: unless-stopped) startar oss paa nytt."""
    time.sleep(forseinking)
    os._exit(kode)


# --- Oppstart --------------------------------------------------------------

def start(web_port: int = 8080, logg_hentar=None) -> None:
    """Start berge-serveren i bakgrunnstraadar. Kallast tidleg i node-main.

    Feilar aldri utover: ein node skal starte sjoelv om bergevegen ikkje
    kan bindast.
    """
    global _web_port, _logg_hentar
    _web_port = int(web_port)
    _logg_hentar = logg_hentar

    port = int(os.environ.get("BERGE_PORT", 8081))

    def _koeyr():
        forsoek = 0
        while True:
            try:
                srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
                srv.daemon_threads = True
                import logging
                logging.getLogger("berge").info(
                    f"Berge-server lyttar paa {port} (reserveveg naar {_web_port} er nede)")
                srv.serve_forever()
            except Exception:
                forsoek += 1
                try:
                    import logging
                    logging.getLogger("berge").error(
                        f"Berge-server kunne ikkje starte (forsoek {forsoek}): "
                        f"{traceback.format_exc()}")
                except Exception:
                    pass
            time.sleep(15)

    threading.Thread(target=_koeyr, daemon=True, name="berge-http").start()
    threading.Thread(target=_vakt_loop, daemon=True, name="berge-vakt").start()
