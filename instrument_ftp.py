# -*- coding: utf-8 -*-
"""Hent måledata frå eit instrument sin FTP-server.

Elspec G4500 strøymer ikkje måledata over nettet. Modbus TCP er ikkje open
på nokon port, og serieporten står i ELCOM OPC-gateway-modus (sjå
`nett_skann` + G4500-doket). Det som ER der, er FTP-arkivet.

**Fella som må handterast først:** instrumentet svarar på PASV med SI EIGA
adresse. Vi når han gjennom NAT-en som `10.99.0.1`, men han seier
`192.168.1.1` — som gjennom NAT-en peikar på ei heilt anna maskin, og på
eit kunde-LAN gjerne på ei som faktisk finst. Datakanalen ville då enten
hengje eller lande feil stad. Vi held difor på verten vi ringde.

Rein stdlib (`ftplib`) — ingen nye avhengnader inn i imaget.
"""
from __future__ import annotations

import ftplib
import json
import os
import re
import threading
import time

KONFIG_FIL = "/data/konfig/instrument_ftp.json"

STANDARD = {
    "aktivert": False,
    "vert": "",
    "port": 21,
    "brukar": "ftpuser",
    "passord": "ftppassword",
    "rot": "/",
    "timeout_s": 25,
    # Kor ofte vi ser etter nye filer. Instrumentet skriv ei fil med jamne
    # mellomrom, so det nyttar ikkje aa polle raskare enn han skriv.
    "intervall_min": 10,
    "maalkatalog": "/data/maalingar/instrument",
    # Tomt = alle filer. Elles eit enkelt glob-mønster, t.d. "*.csv".
    "monster": "",
    # Prefiks framfor kanalnamna frå CSV-en. Fleire instrument på same node
    # ville elles fått same kanalnamn ("Frequency_Avg" osv.).
    "kanal_prefiks": "",
    # Berre nyaste rapport per type (DL/MR ...) i staden for heile arkivet.
    # For KANALAR er det alt vi treng, og ein skjør innebygd server toler
    # to nedlastingar langt betre enn seksti. På = kanaldrift; av = full
    # arkivering av alle filer.
    "berre_nyaste": True,
    # Kor mange nedlasta filer vi hugsar, so vi ikkje hentar same fila om
    # att. Instrumentet har ikkje noko "ny sidan"-omgrep.
    "hugs_filer": 5000,
}

# Ei FTP-økt kan ikkje delast mellom trådar, og eit innebygd instrument
# toler få samtidige. Ein om gongen.
_las = threading.Lock()


class _Klient(ftplib.FTP):
    """FTP-klient som held på verten vi faktisk kopla til.

    Standard ftplib brukar adressa frå 227-svaret. Bak NAT er den adressa
    instrumentet sitt eige syn på seg sjølv, ikkje ein veg dit frå oss.
    """

    def makepasv(self):
        _vert, havn = super().makepasv()
        return self.host, havn


def _privat(vert: str) -> bool:
    """Berre private IP-ar, som i instrument-proxyen.

    Utan denne kan kven som helst med hub-tilgang få noden til å logge inn
    på ein vilkårleg FTP-server ute på nettet.
    """
    try:
        import instrument_proxy
        return instrument_proxy.tillat_vert(vert)
    except Exception:
        return False


# --- Konfig ----------------------------------------------------------
def les_konfig() -> dict:
    k = dict(STANDARD)
    try:
        with open(KONFIG_FIL, "r", encoding="utf-8") as f:
            k.update(json.load(f) or {})
    except Exception:
        pass
    return k


def lagre_konfig(data: dict) -> tuple:
    """(ok, melding). Validerer før vi skriv."""
    k = les_konfig()
    vert = str(data.get("vert", k["vert"])).strip()
    if vert and not _privat(vert):
        return False, "'%s' is not a private IP address" % vert
    try:
        port = int(data.get("port", k["port"]))
        intervall = float(data.get("intervall_min", k["intervall_min"]))
    except (TypeError, ValueError):
        return False, "Invalid port or interval"
    if not 1 <= port <= 65535:
        return False, "Port %d out of range" % port
    if not 0.5 <= intervall <= 1440:
        return False, "Interval must be between 0.5 and 1440 minutes"
    k.update({
        "aktivert": bool(data.get("aktivert", k["aktivert"])),
        "vert": vert,
        "port": port,
        "brukar": str(data.get("brukar", k["brukar"])),
        "rot": str(data.get("rot", k["rot"])) or "/",
        "intervall_min": intervall,
        "maalkatalog": str(data.get("maalkatalog", k["maalkatalog"])),
        "monster": str(data.get("monster", k["monster"])),
        "kanal_prefiks": str(data.get("kanal_prefiks", k["kanal_prefiks"])),
        "berre_nyaste": bool(data.get("berre_nyaste", k["berre_nyaste"])),
    })
    # Tomt passord frå GUI-et tyder «ikkje endra» — elles ville kvar lagring
    # av eit skjema som ikkje viser passordet, slette det.
    nytt = data.get("passord")
    if nytt:
        k["passord"] = str(nytt)
    try:
        os.makedirs(os.path.dirname(KONFIG_FIL), exist_ok=True)
        with open(KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(k, f, indent=2)
    except Exception as e:
        return False, "Could not save: %s" % e
    return True, "Saved"


def konfig_offentleg() -> dict:
    """Konfig utan passordet — det skal ikkje ut i eit API-svar."""
    k = les_konfig()
    k["passord_sett"] = bool(k.pop("passord", ""))
    return k


# --- Tilkopling ------------------------------------------------------
def _opne(vert: str = "", port: int = 0, brukar: str = "", passord: str = "",
          timeout: float = 0) -> _Klient:
    k = les_konfig()
    vert = (vert or k["vert"]).strip()
    if not vert:
        raise ValueError("No FTP host configured")
    if not _privat(vert):
        raise ValueError("'%s' is not a private IP address" % vert)
    f = _Klient()
    f.connect(vert, int(port or k["port"]),
              timeout=float(timeout or k["timeout_s"]))
    f.login(brukar or k["brukar"], passord or k["passord"])
    f.set_pasv(True)
    return f


# --- Katalogliste ----------------------------------------------------
# VxWorks svarar med unix-aktig LIST. Vi tek imot både den og MS-DOS-
# varianten, men held alltid på råteksten: eit format vi ikkje kjenner
# skal vere synleg i GUI-et, ikkje forsvinne i ein parser.
_UNIX = re.compile(
    r"^(?P<t>[-dl])\S{9}\s+\S+\s+\S+\s+\S+\s+(?P<storleik>\d+)\s+"
    r"(?P<dato>\S+\s+\S+\s+\S+)\s+(?P<namn>.+)$")
_DOS = re.compile(
    r"^(?P<dato>\d[\d/-]+\s+[\d:]+(?:\s*[AP]M)?)\s+"
    r"(?:<DIR>|(?P<storleik>\d+))\s+(?P<namn>.+)$", re.I)


def _tolk_linje(linje: str) -> dict:
    m = _UNIX.match(linje)
    if m:
        return {"namn": m.group("namn").strip(),
                "storleik": int(m.group("storleik")),
                "dato": m.group("dato"),
                "katalog": m.group("t") == "d",
                "raa": linje}
    m = _DOS.match(linje)
    if m:
        return {"namn": m.group("namn").strip(),
                "storleik": int(m.group("storleik") or 0),
                "dato": m.group("dato"),
                "katalog": m.group("storleik") is None,
                "raa": linje}
    return {"namn": linje.strip(), "storleik": 0, "dato": "",
            "katalog": False, "raa": linje, "ukjend_format": True}


def _sti(mappe: str, namn: str) -> str:
    if not mappe or mappe == "/":
        return "/" + namn.lstrip("/")
    return mappe.rstrip("/") + "/" + namn.lstrip("/")


def liste(sti: str = "", vert: str = "", **kv) -> dict:
    """Innhaldet i ein katalog på instrumentet."""
    k = les_konfig()
    sti = sti or k["rot"] or "/"
    with _las:
        f = _opne(vert, **kv)
        try:
            velkomst = (f.getwelcome() or "").strip()
            try:
                syst = f.sendcmd("SYST")
            except Exception:
                syst = ""
            linjer = []
            f.retrlines("LIST " + sti if sti != "/" else "LIST", linjer.append)
        finally:
            _lukk(f)
    oppf = [_tolk_linje(x) for x in linjer if x.strip()]
    oppf = [o for o in oppf if o["namn"] not in (".", "..")]
    return {"suksess": True, "sti": sti, "velkomst": velkomst, "syst": syst,
            "oppforingar": oppf}


def _lukk(f) -> None:
    try:
        f.quit()
    except Exception:
        try:
            f.close()
        except Exception:
            pass


def hent_bytes(sti: str, maks: int = 65536, vert: str = "", **kv) -> dict:
    """Hent starten av ei fil, for å sjå kva format ho er i.

    Måledata frå Elspec er PQZIP — eit lukka format. Før vi byggjer noko
    rundt filene må vi vite kva som faktisk ligg der, og då er dei første
    kilobytane med magiske tal meir verdt enn all dokumentasjonen.
    """
    bitar = bytearray()

    def ta(b):
        if len(bitar) < maks:
            bitar.extend(b[:maks - len(bitar)])

    with _las:
        f = _opne(vert, **kv)
        try:
            try:
                storleik = f.size(sti)
            except Exception:
                storleik = None
            f.retrbinary("RETR " + sti, ta, blocksize=8192)
        finally:
            _lukk(f)
    b = bytes(bitar)
    return {"suksess": True, "sti": sti, "storleik": storleik,
            "lest": len(b), "hex": b[:96].hex(),
            "tekst": b[:4096].decode("utf-8", "replace")}


def hent_til_fil(sti: str, maal: str, vert: str = "", **kv) -> dict:
    """Last ned ei fil til noden.

    Vi skriv til `.del` og byter namn til slutt: ei halvferdig fil med rett
    namn ser ut som ei ferdig fil for alt som les katalogen etterpå.
    """
    os.makedirs(os.path.dirname(maal) or ".", exist_ok=True)
    mellom = maal + ".del"
    start = time.time()
    with _las:
        f = _opne(vert, **kv)
        try:
            with open(mellom, "wb") as ut:
                f.retrbinary("RETR " + sti, ut.write, blocksize=32768)
        finally:
            _lukk(f)
    os.replace(mellom, maal)
    return {"suksess": True, "sti": sti, "maal": maal,
            "bytes": os.path.getsize(maal),
            "brukt_s": round(time.time() - start, 1)}


def banner(vert: str = "", port: int = 0, timeout: float = 0) -> dict:
    """Kople til utan å logge inn — kva slags server er dette?

    530 fortel berre at innlogginga vart avvist; det skil ikkje feil
    passord frå ein avslått FTP-konto. Velkomstlinja gjer det ofte klart,
    og ho kjem før USER/PASS. Vi loggar IKKJE inn her, so ingen mislukka
    forsøk som kan låse ein konto på innebygd utstyr.
    """
    k = les_konfig()
    vert = (vert or k["vert"]).strip()
    if not vert:
        return {"suksess": False, "melding": "No FTP host configured"}
    if not _privat(vert):
        return {"suksess": False,
                "melding": "'%s' is not a private IP address" % vert}
    f = _Klient()
    try:
        f.connect(vert, int(port or k["port"]),
                  timeout=float(timeout or k["timeout_s"]))
        vel = (f.getwelcome() or "").strip()
    except Exception as e:
        return {"suksess": False, "melding": str(e)}
    finally:
        try:
            f.close()
        except Exception:
            pass
    return {"suksess": True, "velkomst": vel,
            "melding": "Reached FTP server: %s" % (vel or "(no banner)")}


def slett(sti: str, vert: str = "", **kv) -> dict:
    """Slett éi fil (DELE) på instrumentet. Katalog → RMD.

    Destruktivt — kallaren (GUI) må stadfeste. Vi rører berre den eine
    stien som blir send inn.
    """
    if not sti or sti in ("/", ""):
        return {"suksess": False, "melding": "Refusing to delete root"}
    with _las:
        f = _opne(vert, **kv)
        try:
            try:
                f.delete(sti)
                return {"suksess": True, "melding": "Deleted %s" % sti}
            except ftplib.error_perm as e:
                m = str(e)
                # Kanskje ein katalog — prøv RMD.
                if m.startswith("550"):
                    try:
                        f.rmd(sti)
                        return {"suksess": True, "melding": "Removed directory %s" % sti}
                    except Exception:
                        pass
                return {"suksess": False, "melding": m}
        finally:
            _lukk(f)


def slett_stotta(vert: str = "", **kv) -> dict:
    """Non-destruktiv sjekk: støttar serveren DELE i det heile?

    Prøver å slette eit namn som ikkje finst og les svaret. «No such file»
    tyder at DELE er lov (berre fila mangla); «not implemented»/«permission»
    tyder at det ikkje går. Ingen ekte fil blir rørt.
    """
    with _las:
        f = _opne(vert, **kv)
        try:
            probe = "/pqtech_delete_probe_%d.tmp" % int(time.time())
            try:
                f.delete(probe)
                return {"stotta": True, "melding": "DELE accepted"}
            except ftplib.error_perm as e:
                m = str(e)
                lav = m.lower()
                nekta = ("permission" in lav or "denied" in lav
                         or m.startswith(("500", "502", "504")))
                mangla = ("no such" in lav or "not found" in lav
                          or "cannot find" in lav or m.startswith("550"))
                return {"stotta": bool(mangla and not nekta), "melding": m}
            except Exception as e:
                return {"stotta": False, "melding": str(e)}
        finally:
            _lukk(f)


def test(vert: str = "", **kv) -> dict:
    """Kom vi inn, og kva svarar serveren?

    Ved innloggingsfeil tek vi med velkomstbanneret likevel, so brukaren
    ser kva server det er og kan skilje feil passord frå ein avslått konto.
    """
    try:
        res = liste("", vert, **kv)
    except Exception as e:
        svar = {"suksess": False, "melding": str(e)}
        b = banner(vert)
        if b.get("velkomst"):
            svar["velkomst"] = b["velkomst"]
        return svar
    n = len(res["oppforingar"])
    return {"suksess": True, "velkomst": res["velkomst"], "syst": res["syst"],
            "melding": "Connected, %d %s in %s"
                       % (n, "entry" if n == 1 else "entries", res["sti"])}


# --- Synkronisering --------------------------------------------------
# Instrumentet har ikkje noko "gi meg det nye" — vi må sjølve hugse kva vi
# har henta. Namn + storleik er nok: filene får unike namn med tidsstempel,
# og ei fil som veks er ikkje ferdigskriven enno.
HENTA_FIL = "/data/konfig/instrument_ftp_henta.json"

_tilstand = {
    "tilstand": "",          # '' | 'koeyrer' | 'ok' | 'feil'
    "melding": "",
    "sist_forsok": None,
    "sist_ok": None,
    "nye_sist": 0,
    "totalt_henta": 0,
    "neste_om_s": None,
    "kanalar": 0,
    "kanal_detaljar": {},
}
_stopp = threading.Event()
_traad = None


def status() -> dict:
    ut = dict(_tilstand)
    ut["henta_kjende"] = len(_les_henta())
    return ut


def _les_henta() -> dict:
    try:
        with open(HENTA_FIL, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _skriv_henta(d: dict) -> None:
    k = les_konfig()
    maks = int(k.get("hugs_filer", 5000))
    if len(d) > maks:
        # Eldste først ut. Verdien er (storleik, tid) — vi sorterer på tid.
        for nokkel in sorted(d, key=lambda x: d[x][1])[:len(d) - maks]:
            d.pop(nokkel, None)
    try:
        os.makedirs(os.path.dirname(HENTA_FIL), exist_ok=True)
        with open(HENTA_FIL, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _passar(namn: str, monster: str) -> bool:
    if not monster:
        return True
    import fnmatch
    return fnmatch.fnmatch(namn.lower(), monster.lower())


def _nlst_med_retry(f, mappe: str, forsok: int = 4):
    """Namn i ein katalog via NLST, med retry. Returnerer (namn, klient).

    NLST sender berre filnamn, ikkje fulle stat-linjer som LIST. På ein
    stor katalog er det ein mykje mindre dataoverføring, og ein skjør
    innebygd server (VxWorks hos Elspec) reset han langt sjeldnare. Vi
    brukar han til å FINNE rapportane; storleik/dato hentar vi berre for
    dei få vi faktisk lastar ned.
    """
    mp = mappe if mappe and mappe != "/" else ""
    for i in range(forsok):
        try:
            namn = f.nlst(mp) if mp else f.nlst()
            # Nokre serverar tek med full sti, andre berre namnet.
            return [n.rsplit("/", 1)[-1] for n in namn], f
        except ftplib.all_errors as e:
            if i == forsok - 1:
                raise
            time.sleep(1.0 + i)
            _lukk(f)
            try:
                f = _opne()
            except Exception:
                raise e
    return [], f


def _storleik(f, sti: str):
    """SIZE på ei fil — ein liten kontroll-kommando, ingen datakanal.

    Trygg der ein LIST av heile katalogen reset: vi spør berre om dei få
    filene vi vil ha.
    """
    try:
        return f.size(sti)
    except Exception:
        return None


def _list_med_retry(f, mappe: str, forsok: int = 3):
    """LIST med retry på reset.

    Innebygde FTP-serverar (VxWorks hos Elspec) reset gjerne datakanalen
    når dei er travle. Ei ny tilkopling og eit nytt forsøk hjelper som
    oftast. Vi returnerer (linjer, klient) — klienten kan vere bytt ut.
    """
    for i in range(forsok):
        linjer = []
        try:
            f.retrlines("LIST " + mappe if mappe != "/" else "LIST",
                        linjer.append)
            return linjer, f
        except ftplib.all_errors as e:
            if i == forsok - 1:
                raise
            time.sleep(1.0 + i)
            try:
                f = _opne()
            except Exception:
                raise e
    return [], f


def _finn_filer(f, mappe: str, monster: str, djup: int = 0,
                maks_djup: int = 3) -> list:
    """Alle filer under `mappe`, rekursivt.

    Vi går ikkje djupare enn `maks_djup`: eit instrument med ein
    symlink-lykkje ville elles halde oss der til timeouten.
    """
    try:
        linjer, _ = _list_med_retry(f, mappe)
    except Exception:
        return []
    ut = []
    for ln in linjer:
        o = _tolk_linje(ln)
        if o["namn"] in (".", ".."):
            continue
        full = _sti(mappe, o["namn"])
        if o["katalog"]:
            if djup < maks_djup:
                ut.extend(_finn_filer(f, full, monster, djup + 1, maks_djup))
        elif _passar(o["namn"], monster):
            ut.append({"sti": full, "namn": o["namn"],
                       "storleik": o["storleik"], "dato": o["dato"]})
    return ut


# Berre éin synk om gongen. Loekka og eit manuelt "hent no" kan elles gaa
# oppaa kvarandre: begge les henta-lista, begge lastar ned alt, og
# status-teljarane blir sjoelvmotseiande.
_synk_gaar = threading.Lock()


def synk_ein_gong() -> dict:
    """Sjå etter nye filer og hent dei. Returnerer kva som vart henta."""
    k = les_konfig()
    if not k["vert"]:
        return {"suksess": False, "melding": "No FTP host configured"}
    if not _synk_gaar.acquire(blocking=False):
        return {"suksess": False, "melding": "A sync is already running",
                "nye": [], "feila": [], "sett": 0}
    try:
        return _synk_innmat(k)
    finally:
        _synk_gaar.release()


def _rapport_type(namn: str) -> str:
    """"DL log 2023_… .csv" -> "DL log"."""
    return namn.split(" 20", 1)[0].strip() or namn


def _samle_rapportfiler(f, mappe: str, monster: str, djup: int = 0,
                        maks_djup: int = 3) -> list:
    """Rekursivt: alle rapport-/CSV-filer under `mappe` (òg i undermapper).

    BlackBox-en legg rapportane i undermapper (t.d. dato-mapper under
    /CF_UPMB/…), so vi må gå ned i katalogtreet — ikkje berre den eine mappa
    vi fekk beskjed om. Vi brukar LIST (med retry) for å kjenne att kataloger
    vs filer, og stoppar på `maks_djup` mot symlink-lykkjer. Storleiken kjem
    frå LIST-linja, so vi slepp eigne SIZE-kall.
    """
    try:
        linjer, _ = _list_med_retry(f, mappe)
    except Exception:
        return []
    ut = []
    for ln in linjer:
        o = _tolk_linje(ln)
        n = o["namn"]
        if n in (".", ".."):
            continue
        if o["katalog"]:
            if djup < maks_djup:
                ut.extend(_samle_rapportfiler(f, _sti(mappe, n), monster,
                                              djup + 1, maks_djup))
            continue
        # Rapportfiler ("DL log …"/"MR log …") tel med jamvel utan .csv (den
        # aktive loggen er open og har enno ikkje fått endinga). Elles krev
        # vi .csv, eller eit eige mønster om brukaren har sett eit.
        er_rapport = n.startswith("DL log") or n.startswith("MR log")
        if not er_rapport and monster and not _passar(n, monster):
            continue
        if not er_rapport and not n.lower().endswith(".csv"):
            continue
        ut.append({"sti": _sti(mappe, n), "namn": n,
                   "storleik": o.get("storleik") or 0, "dato": o.get("dato", "")})
    return ut


def _nyaste_fjern(f, mappe: str, monster: str) -> list:
    """Nyaste rapport per type på instrumentet — rekursivt gjennom undermapper.

    Samlar alle rapport-/CSV-filer i heile treet, grupperer på type ("DL log",
    "MR log", …) og vel den med seinast sluttid i namnet. So slepp vi å laste
    ned alle dei gamle — berre den ferskaste per type — men no òg frå
    undermappene, ikkje berre rot-mappa.
    """
    alle = _samle_rapportfiler(f, mappe, monster)
    beste: dict = {}
    for fil in alle:
        slutt = _rapport_slutt(fil["namn"])
        typ = _rapport_type(fil["namn"])
        if typ not in beste or slutt > beste[typ][0]:
            beste[typ] = (slutt, fil)
    ut = []
    for _typ, (_slutt, fil) in beste.items():
        # LIST manglar av og til storleik — fyll med SIZE for dei få valde.
        if not fil["storleik"]:
            fil["storleik"] = _storleik(f, fil["sti"]) or 0
        ut.append(fil)
    return ut


def _synk_innmat(k: dict) -> dict:
    henta = _les_henta()
    maalkat = k["maalkatalog"]
    nye, feila, filer = [], [], []
    with _las:
        f = _opne()
        try:
            if k.get("berre_nyaste", True):
                filer = _nyaste_fjern(f, k["rot"] or "/", k["monster"])
            else:
                filer = _finn_filer(f, k["rot"] or "/", k["monster"])
            for n_gjort, fil in enumerate(filer):
                nokkel = "%s|%d" % (fil["sti"], fil["storleik"])
                if nokkel in henta:
                    continue
                maal = os.path.join(maalkat, fil["sti"].lstrip("/"))
                os.makedirs(os.path.dirname(maal) or ".", exist_ok=True)
                mellom = maal + ".del"
                # Innebygde FTP-serverar reset gjerne datakanalen når dei har
                # gjort mange overføringar på rad. Vi prøver på nytt med ei
                # frisk tilkopling i staden for å gje opp heile synken.
                ok = False
                siste_feil = "unknown error"
                for forsok in range(3):
                    try:
                        with open(mellom, "wb") as ut:
                            f.retrbinary("RETR " + fil["sti"], ut.write,
                                         blocksize=32768)
                        os.replace(mellom, maal)
                        ok = True
                        break
                    except ftplib.all_errors as e:
                        try:
                            os.unlink(mellom)
                        except Exception:
                            pass
                        siste_feil = e
                        if forsok < 2:
                            time.sleep(1.5 + forsok)
                            _lukk(f)
                            try:
                                f = _opne()
                            except Exception:
                                break
                if not ok:
                    feila.append({"sti": fil["sti"], "feil": str(siste_feil)})
                    continue
                henta[nokkel] = [fil["storleik"], time.time()]
                nye.append({"sti": fil["sti"], "maal": maal,
                            "bytes": fil["storleik"]})
                # Pust litt mellom filene: hamrar vi på ein liten innebygd
                # server, går han i kne. Ei kort pause kvar fjerde fil held
                # han med.
                if n_gjort % 4 == 3:
                    time.sleep(0.4)
        finally:
            _lukk(f)
    _skriv_henta(henta)
    _tilstand["totalt_henta"] += len(nye)
    # Oppdater kanalane frå dei ferskaste rapportane. Gjer det alltid, ikkje
    # berre når noko nytt kom: ei rapportfil VEKS mellom synkane (same namn,
    # ny siste rad), og den nye storleiken kjem inn som ei "ny" fil uansett.
    try:
        oppdater_kanalar()
    except Exception:
        pass
    return {"suksess": True, "nye": nye, "feila": feila, "sett": len(filer),
            "melding": "%d new file(s)%s"
                       % (len(nye), ", %d failed" % len(feila) if feila else "")}


# --- CSV -> kanalverdiar ---------------------------------------------
# Elspec-rapportane er reine CSV: ei valfri "Device Name:"-linje, so ei
# hovudlinje som byrjar med "UTC Time", so datarader. Siste rad er ferdige
# 15-min-verdiar. Kvar talkolonne (utanom tid) blir ein kanal.
#
# PQZIP-arkivet er lukka og let vi vere - CSV-rapportane har det vi treng
# for kanalar: effekt, frekvens, energiteljarar.
_siste_kanalar: dict = {}
_kanal_las = threading.Lock()
_TID_KOLONNAR = ("utc time", "local time", "time", "date", "timestamp")


def _tal(s: str):
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_rapport(lokalsti: str) -> tuple:
    """(tidsstempel_tekst, {kolonne: verdi}) frå siste rad i ein CSV-rapport.

    Vi les heile fila - rapportane er nokre hundre kB, og vi vil ha SISTE
    rad. Ei linje som ikkje har like mange felt som hovudet (ei halvskriven
    siste linje) blir hoppa over.
    """
    try:
        import csv
        with open(lokalsti, "r", encoding="utf-8", errors="replace",
                  newline="") as f:
            rader = list(csv.reader(f))
    except Exception:
        return "", {}
    # Finn hovudlinja (kan ha ei "Device Name:"-linje foer)
    hovud_i = None
    for i, rad in enumerate(rader[:5]):
        if rad and rad[0].strip().lower() in _TID_KOLONNAR:
            hovud_i = i
            break
    if hovud_i is None:
        return "", {}
    hovud = [c.strip() for c in rader[hovud_i]]
    siste = None
    for rad in rader[hovud_i + 1:]:
        if len(rad) == len(hovud) and any(c.strip() for c in rad):
            siste = rad
    if siste is None:
        return "", {}
    tid = siste[0].strip()
    ut = {}
    for kol, verdi in zip(hovud, siste):
        if kol.lower() in _TID_KOLONNAR:
            continue
        t = _tal(verdi)
        if t is not None:
            ut[kol] = t
    return tid, ut


# Tidsstempel i eit rapportnamn: "2022_11_01 12_00_00" (start og/eller slutt).
_TS = re.compile(r"(\d{4})_(\d{2})_(\d{2}) (\d{2})_(\d{2})_(\d{2})")
# Sluttidspunktet spesifikt: det som kjem etter " to ".
_SLUTT = re.compile(r" to (\d{4})_(\d{2})_(\d{2}) (\d{2})_(\d{2})_(\d{2})")


def _ts(m) -> float:
    import calendar
    try:
        return float(calendar.timegm(tuple(int(x) for x in m.groups())
                                     + (0, 0, 0)))
    except Exception:
        return -1.0


def _rapport_slutt(base: str) -> float:
    """Rangeringsnøkkel for kor fersk ein rapport er, frå filnamnet.

    Vi kan IKKJE bruke mtime: synken lastar ned heile mappa på ein gong, so
    alle filene får same mtime og "nyaste" blir tilfeldig.

    Ein rapport "A to B" rangerer på SLUTTEN B — perioden han dekkjer. Ein
    open/aktiv logg "A to" (utan sluttdato enno) rangerer på starten A, so
    ein logg som nettopp er starta vinn over ein eldre lukka rapport. Vi
    tek slutten spesifikt (etter " to "), ikkje berre "seinaste tal i
    namnet": ei korrupt fil "2023_03 to 2021_01" (slutt før start) skal
    rangere på 2021, ikkje lurast fram av start-2023.
    """
    m = _SLUTT.search(base)
    if m:
        return _ts(m)
    # Ingen sluttdato: open logg. Bruk starten (første tidsstempel).
    m = _TS.search(base)
    return _ts(m) if m else -1.0


def _nyaste_per_type(maalkat: str) -> list:
    """Nyaste lokale fil for kvar rapport-type (DL/MR/…).

    Filnamna byrjar med typen ("DL log …", "MR log …") og har periodens
    sluttid i seg. Vi vil ha éin kanalsett per type, frå rapporten som
    dekkjer den seinaste perioden.
    """
    import glob
    beste: dict = {}
    stiar = set()
    for m in ("*.csv", "DL log*", "MR log*"):
        stiar.update(glob.glob(os.path.join(maalkat, "**", m), recursive=True))
    for sti in stiar:
        if os.path.isdir(sti):
            continue
        base = os.path.basename(sti)
        # "DL log 2022_… .csv" -> "DL log", elles heile namnet
        type_ = base.split(" 20", 1)[0].strip() or base
        try:
            mtid = os.path.getmtime(sti)
        except OSError:
            mtid = 0.0
        # Primær nøkkel: sluttid i namnet. Fell tilbake til mtime når namnet
        # ikkje har eit tidsstempel.
        nokkel = (_rapport_slutt(base), mtid)
        if type_ not in beste or nokkel > beste[type_][0]:
            beste[type_] = (nokkel, sti)
    return [(t, v[1]) for t, v in beste.items()]


def oppdater_kanalar() -> dict:
    """Les nyaste rapportar og oppdater kanal-cachen. Returnerer verdiane."""
    k = les_konfig()
    prefiks = str(k.get("kanal_prefiks", "") or "")
    nye = {}
    detaljar = {}
    for type_, sti in _nyaste_per_type(k["maalkatalog"]):
        tid, verdiar = _parse_rapport(sti)
        if not verdiar:
            continue
        detaljar[type_] = {"fil": os.path.basename(sti), "tid": tid,
                           "kanalar": len(verdiar)}
        for kol, verdi in verdiar.items():
            nye[prefiks + kol] = verdi
    with _kanal_las:
        _siste_kanalar.clear()
        _siste_kanalar.update(nye)
    _tilstand["kanal_detaljar"] = detaljar
    _tilstand["kanalar"] = len(nye)
    return dict(nye)


def siste_kanalverdiar() -> dict:
    """Siste kanalverdiar frå rapportane. Brukt av push-straumen.

    Namna er kolonnenamna frå CSV-en (t.d. "kW_Total_Avg", "kWh in"),
    eventuelt med eit konfigurert prefiks.
    """
    with _kanal_las:
        return dict(_siste_kanalar)


def _loop() -> None:
    k = les_konfig()
    # Vent litt ved oppstart: nettet (og NAT-en) er ikkje nødvendigvis klart
    # i det containeren startar.
    _stopp.wait(45)
    # Rapportar frå ein tidlegare synk kan alt ligge på disk - fyll cachen
    # med ein gong so kanalane finst før første nye nedlasting.
    try:
        oppdater_kanalar()
    except Exception:
        pass
    while not _stopp.is_set():
        k = les_konfig()
        if not k["aktivert"] or not k["vert"]:
            _stopp.wait(60)
            continue
        _tilstand.update(tilstand="koeyrer", melding="Checking for new files",
                         sist_forsok=time.time())
        try:
            res = synk_ein_gong()
            _tilstand.update(tilstand="ok" if res["suksess"] else "feil",
                             melding=res.get("melding", ""),
                             nye_sist=len(res.get("nye", [])),
                             sist_ok=time.time() if res["suksess"] else
                             _tilstand["sist_ok"])
        except Exception as e:
            _tilstand.update(tilstand="feil", melding=str(e))
        vent = max(30.0, float(k["intervall_min"]) * 60.0)
        _tilstand["neste_om_s"] = vent
        _stopp.wait(vent)


def start_synk() -> None:
    """Start bakgrunnsløkka. Trygg å kalle fleire gonger."""
    global _traad
    if _traad is not None and _traad.is_alive():
        return
    _stopp.clear()
    _traad = threading.Thread(target=_loop, name="instrument-ftp", daemon=True)
    _traad.start()


def stopp_synk() -> None:
    _stopp.set()
