#!/usr/bin/env python3
"""
API-nøklar for ekstern lesetilgang
==================================
Lèt eksterne klientar — t.d. ein desktop-widget på ei maskin utanfor
hub-nettet — lese måledata over HTTPS utan sesjons-innlogging.

Designval:

* Nøkkelen vert vist ÉIN gong ved oppretting, og lagra berre som SHA-256-hash.
  Kjem konfigfila på avvegar, gir han ingen tilgang. (Nøklane er 192-bit
  tilfeldige, så rein SHA-256 held — vi treng ingen langsam KDF slik som for
  menneskevalde passord.)
* Kvar nøkkel kan trekkjast tilbake for seg, deaktiverast, ha utløpsdato, og
  avgrensast til utvalde kanalar. Det er skilnaden frå den delte flåte-nøkkelen
  (`_floate_token`), som er alt-eller-ingenting og ikkje kan revokerast utan å
  bryte heile flåten.
* Alle nøklar er READ-ONLY. `scope`-feltet ligg der for framtidig utviding,
  men API-laget slepp berre GET gjennom.
"""

import os
import json
import time
import hashlib
import secrets
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import List, Optional

KONFIG_DIR = "/data/konfig"
NOKKEL_FIL = os.path.join(KONFIG_DIR, "api_nokler.json")

PREFIKS = "pqt_"            # gjer nøkkelen attkjenneleg i loggar og GUI
_PREFIKS_VIS = 12           # kor mange teikn av nøkkelen vi viser i GUI
_SIST_BRUKT_THROTTLE_S = 60  # skriv «sist brukt» maks ein gong per minutt

_laas = threading.Lock()
_cache = {"mtime": 0.0, "data": None}
_sist_skrive = {}           # nokkel_id -> monotonic ts for sist_brukt-skriving


@dataclass
class ApiNokkel:
    """Ein API-nøkkel slik han vert lagra (utan klartekst)."""
    id: str
    namn: str
    prefiks: str                      # fyrste teikn av nøkkelen, for attkjenning
    hash: str
    oppretta: str                     # ISO-tidsstempel
    sist_brukt: str = ""
    aktivert: bool = True
    utloep: str = ""                  # ISO-dato (YYYY-MM-DD), tom = aldri
    kanal_filter: List[str] = field(default_factory=list)  # tomt = alle kanalar
    scope: str = "les"                # read-only; feltet er for framtidig bruk

    def til_dict(self) -> dict:
        return asdict(self)

    def offentleg(self) -> dict:
        """Utan hash — trygt å sende til GUI-et."""
        d = self.til_dict()
        d.pop("hash", None)
        return d

    def utgaatt(self) -> bool:
        if not self.utloep:
            return False
        try:
            return date.fromisoformat(self.utloep[:10]) < date.today()
        except ValueError:
            return False   # ugyldig dato låser ikkje ute nøkkelen

    def gyldig(self) -> bool:
        return self.aktivert and not self.utgaatt()

    @classmethod
    def fraa_dict(cls, d: dict) -> "ApiNokkel":
        return cls(
            id=str(d.get("id", "")),
            namn=str(d.get("namn", "")),
            prefiks=str(d.get("prefiks", "")),
            hash=str(d.get("hash", "")),
            oppretta=str(d.get("oppretta", "")),
            sist_brukt=str(d.get("sist_brukt", "")),
            aktivert=bool(d.get("aktivert", True)),
            utloep=str(d.get("utloep", "") or ""),
            kanal_filter=[str(x) for x in (d.get("kanal_filter") or [])],
            scope=str(d.get("scope", "les")),
        )


def _hash(nokkel: str) -> str:
    return hashlib.sha256(nokkel.encode("utf-8")).hexdigest()


def les_nokler() -> List[ApiNokkel]:
    """Les nøklane frå disk. mtime-cacha — dette er ein hot path (kvart kall
    mot /api/v1/ går gjennom her)."""
    try:
        mtime = os.path.getmtime(NOKKEL_FIL)
    except OSError:
        _cache["mtime"] = 0.0
        _cache["data"] = []
        return []

    if _cache["data"] is not None and mtime == _cache["mtime"]:
        return _cache["data"]

    try:
        with open(NOKKEL_FIL, "r") as f:
            raa = json.load(f)
        nokler = [ApiNokkel.fraa_dict(d) for d in raa.get("nokler", [])]
    except (OSError, json.JSONDecodeError, TypeError):
        nokler = []

    _cache["mtime"] = mtime
    _cache["data"] = nokler
    return nokler


def _lagre(nokler: List[ApiNokkel]) -> None:
    os.makedirs(KONFIG_DIR, exist_ok=True)
    tmp = NOKKEL_FIL + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"nokler": [n.til_dict() for n in nokler]}, f, indent=2)
    os.replace(tmp, NOKKEL_FIL)     # atomisk, så ei halvskriven fil aldri vert lest
    _cache["mtime"] = 0.0
    _cache["data"] = None


def opprett(namn: str, utloep: str = "", kanal_filter: Optional[List[str]] = None) -> tuple:
    """Opprett ny nøkkel. Returnerer (klartekst_nøkkel, ApiNokkel).

    Klarteksten finst berre i dette returverdiet — han vert aldri lagra, og
    kan difor ikkje hentast fram att seinare.
    """
    namn = (namn or "").strip() or "Namnlaus"
    klartekst = PREFIKS + secrets.token_hex(24)
    nokkel = ApiNokkel(
        id=secrets.token_hex(4),
        namn=namn,
        prefiks=klartekst[:_PREFIKS_VIS],
        hash=_hash(klartekst),
        oppretta=datetime.now().isoformat(timespec="seconds"),
        utloep=(utloep or "").strip(),
        kanal_filter=[str(x).strip() for x in (kanal_filter or []) if str(x).strip()],
    )
    with _laas:
        nokler = list(les_nokler())
        nokler.append(nokkel)
        _lagre(nokler)
    return klartekst, nokkel


def verifiser(klartekst: str) -> Optional[ApiNokkel]:
    """Slå opp ein nøkkel. Returnerer ApiNokkel om han er gyldig, elles None.

    Samanlikninga er konstant-tid, og vi går gjennom ALLE nøklar sjølv etter
    treff, så responstida ikkje lekk kor i lista nøkkelen låg.
    """
    klartekst = (klartekst or "").strip()
    if not klartekst:
        return None
    gitt = _hash(klartekst)
    treff = None
    for n in les_nokler():
        if secrets.compare_digest(n.hash, gitt) and n.gyldig():
            treff = n
    if treff is not None:
        _noter_bruk(treff)
    return treff


def _noter_bruk(nokkel: ApiNokkel) -> None:
    """Oppdater «sist brukt». Throttla, sidan ein widget kan polle kvart
    sekund og vi ikkje vil skrive konfigfila like ofte."""
    no = time.monotonic()
    if no - _sist_skrive.get(nokkel.id, 0.0) < _SIST_BRUKT_THROTTLE_S:
        return
    _sist_skrive[nokkel.id] = no
    try:
        with _laas:
            nokler = list(les_nokler())
            for n in nokler:
                if n.id == nokkel.id:
                    n.sist_brukt = datetime.now().isoformat(timespec="seconds")
                    break
            _lagre(nokler)
    except OSError:
        pass        # «sist brukt» er kosmetikk — aldri bryt eit kall for dette


def slett(nokkel_id: str) -> bool:
    """Trekk tilbake ein nøkkel. Returnerer True om han fanst."""
    with _laas:
        nokler = list(les_nokler())
        att = [n for n in nokler if n.id != nokkel_id]
        if len(att) == len(nokler):
            return False
        _lagre(att)
    return True


def sett_aktivert(nokkel_id: str, aktivert: bool) -> bool:
    """Slå ein nøkkel av/på utan å slette han."""
    with _laas:
        nokler = list(les_nokler())
        for n in nokler:
            if n.id == nokkel_id:
                n.aktivert = bool(aktivert)
                _lagre(nokler)
                return True
    return False


def kanal_synleg(nokkel: ApiNokkel, kanal: dict) -> bool:
    """Slepp denne nøkkelen til denne kanalen?

    Tomt filter = alle kanalar. Elles matchar vi mot kanalnamnet ELLER
    «node/kanal», slik at ein kan avgrense både per kanal og per node:

        ["Sundet/Spenning L1", "Straum L1"]     → to konkrete kanalar
        ["Sundet/*"]                            → alt frå den noden
    """
    if not nokkel.kanal_filter:
        return True
    namn = str(kanal.get("namn") or "")
    node = str(kanal.get("node_namn") or kanal.get("node_id") or "")
    full = f"{node}/{namn}"
    for m in nokkel.kanal_filter:
        m = m.strip()
        if not m:
            continue
        if m.endswith("*"):
            stamme = m[:-1]
            if full.lower().startswith(stamme.lower()) or namn.lower().startswith(stamme.lower()):
                return True
        elif m.lower() in (namn.lower(), full.lower()):
            return True
    return False


def filtrer_kanalar(nokkel: ApiNokkel, kanalar: list) -> list:
    return [k for k in kanalar if kanal_synleg(nokkel, k)]
