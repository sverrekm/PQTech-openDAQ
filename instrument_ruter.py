#!/usr/bin/env python3
"""
Instrument-ruter — gi containeren veg til nett som berre finst paa verten
=========================================================================
Containeren staar paa eit macvlan mot `eth0`. Det gir han eigen IP paa
LAN-et (DewesoftX treng det), men ogsaa eit hardt tak: eit macvlan-
grensesnitt kan ikkje snakke med sin eigen vert, og ser difor ingenting av
dei andre nettverka verten er paa. Koplar du verten til eit instrument-nett
- eit wifi frae ein Elspec BlackBox, eit USB-ethernet mot ein analysator,
eit isolert PQube-segment - saa naar openDAQ og Modbus-pollinga det ikkje.
`nmcli` seier "kopla til", og pollinga timar ut.

Loesinga er eit ekstra bridge-nett paa containeren. Da faar han:

    eth0   macvlan   LAN-identitet + default-rute   (uendra)
    eth1   bridge    veg ut via verten, med NAT

og vi legg inn rute for kvart instrument-subnett via bridge-gatewayen.
Verten rutar vidare ut det grensesnittet nettet faktisk ligg paa, og
Docker sin MASQUERADE tek NAT-en.

Modulen finn bridge-grensesnittet sjoelv (det som IKKJE ber macvlan-IP-en
frae compose), so ingenting maa hardkodast. Han passar ogsaa paa at
default-ruta blir verande paa macvlan - med to nettverk kan Docker elles
leggje ho paa bridgen, og da ville all utgaaende trafikk gaatt gjennom NAT. Manglar bridge-nettet - t.d. fordi
containeren enno ikkje er bygd paa nytt etter compose-endringa - seier vi
det tydeleg i staden for aa feile stille.

MERK: `docker-compose.yml`-endringa foelgjer ikkje med fleet-oppdateringa
(den kopierer berre *.py, entrypoint og frontend/dist). Nytt bridge-nett
krev `docker compose up -d` paa verten ein gong.
"""

import ipaddress
import json
import logging
import os
import subprocess

log = logging.getLogger("instrument_ruter")

KONFIG_FIL = "/data/konfig/instrumentnett.json"


# ---------------------------------------------------------------
#  Konfig
# ---------------------------------------------------------------
def les_konfig() -> dict:
    """{"aktivert": bool, "nett": [{"subnett": "...", "namn": "..."}]}"""
    try:
        with open(KONFIG_FIL, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {"aktivert": True, "nett": []}
    nett = []
    for n in d.get("nett") or []:
        subnett = str(n.get("subnett", "") or "").strip()
        if subnett:
            nett.append({"subnett": subnett,
                         "namn": str(n.get("namn", "") or "").strip()})
    return {"aktivert": bool(d.get("aktivert", True)), "nett": nett}


def lagre_konfig(konfig: dict) -> tuple:
    """Returnerer (ok, melding). Validerer subnetta før lagring."""
    nett = []
    for n in konfig.get("nett") or []:
        subnett = str(n.get("subnett", "") or "").strip()
        if not subnett:
            continue
        try:
            rett = str(ipaddress.ip_network(subnett, strict=False))
        except Exception as e:
            return False, f"Ugyldig subnett «{subnett}»: {e}"
        kol = kolliderer(rett)
        if kol:
            return False, kol
        nett.append({"subnett": rett,
                     "namn": str(n.get("namn", "") or "").strip()})
    ut = {"aktivert": bool(konfig.get("aktivert", True)), "nett": nett}
    try:
        os.makedirs(os.path.dirname(KONFIG_FIL), exist_ok=True)
        with open(KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(ut, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return False, f"Kunne ikkje lagre: {e}"
    return True, f"Lagra {len(nett)} instrumentnett"


# ---------------------------------------------------------------
#  Grensesnitt i containeren
# ---------------------------------------------------------------
def _kjoer(args: list, timeout: float = 10.0):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _default_dev() -> str:
    try:
        r = _kjoer(["ip", "route", "show", "default"])
        for ln in r.stdout.splitlines():
            f = ln.split()
            if "dev" in f:
                return f[f.index("dev") + 1]
    except Exception:
        pass
    return ""


def _macvlan_ip() -> str:
    """IP-en containeren har paa LAN-et (macvlan). Kjem frae compose."""
    return (os.environ.get("OPENDAQ_IP")
            or os.environ.get("CONTAINER_IP") or "").strip()


def grensesnitt() -> list:
    """[(dev, cidr)] for alle IPv4-grensesnitt i containeren."""
    try:
        r = _kjoer(["ip", "-o", "-f", "inet", "addr", "show"])
    except Exception:
        return []
    ut = []
    for ln in r.stdout.splitlines():
        f = ln.split()
        if len(f) >= 4 and f[1] != "lo" and not f[1].startswith("tailscale"):
            ut.append((f[1], f[3]))
    return ut


def macvlan_dev() -> str:
    """Grensesnittet som ber LAN-identiteten vaar."""
    ip = _macvlan_ip()
    for dev, cidr in grensesnitt():
        if ip and cidr.split("/")[0] == ip:
            return dev
    return _default_dev()


def sikre_default_rute() -> str:
    """Hald default-ruta paa macvlan.

    Med to nettverk kan Docker leggje default-ruta paa bridgen. Da ville
    all utgaaende trafikk gaatt gjennom NAT paa verten i staden for rett ut
    paa LAN-et - og openDAQ ville annonsert ein IP han ikkje lenger svarar
    frae. Vi rettar det opp i staden for aa haape.
    """
    mv = macvlan_dev()
    if not mv or _default_dev() == mv:
        return ""
    gw = os.environ.get("NET_GATEWAY", "").strip()
    if not gw:
        for dev, cidr in grensesnitt():
            if dev == mv:
                try:
                    gw = str(next(ipaddress.ip_interface(cidr).network.hosts()))
                except Exception:
                    return ""
                break
    if not gw:
        return ""
    r = _kjoer(["ip", "route", "replace", "default", "via", gw, "dev", mv])
    if r.returncode == 0:
        log.warning(f"Default-ruta laag paa {_default_dev()!r} — flytta "
                    f"tilbake til {mv} via {gw}")
        return f"default flytta til {mv}"
    return (r.stderr or r.stdout or "").strip()


def bru_grensesnitt() -> dict:
    """Finn bridge-grensesnittet — det som IKKJE er macvlan-en.

    Vi identifiserer macvlan-en paa IP-en frae compose, ikkje paa kven som
    har default-ruta: med to nettverk kan Docker leggje default paa bridgen,
    og da ville ei "ikkje default"-regel peikt ut feil grensesnitt.

    Returnerer {"dev", "cidr", "gateway"} eller {} om det ikkje finst.
    """
    mv = macvlan_dev()
    for dev, cidr in grensesnitt():
        if dev == mv:
            continue
        try:
            nett = ipaddress.ip_interface(cidr).network
            # Docker legg gatewayen paa foerste adressa i bridge-subnettet.
            gw = str(next(nett.hosts()))
        except Exception:
            continue
        return {"dev": dev, "cidr": cidr, "gateway": gw}
    return {}


def kolliderer(subnett: str) -> str:
    """Tom streng om subnettet er trygt å rute, elles ei forklaring.

    Eit instrument-nett som overlappar med LAN-et containeren alt står på
    kan ikkje rutast: pakkene ville gått ut feil grensesnitt, eller vi
    ville stole ruta til vår eigen gateway.
    """
    try:
        maal = ipaddress.ip_network(subnett, strict=False)
    except Exception as e:
        return f"Ugyldig subnett: {e}"
    try:
        r = _kjoer(["ip", "-o", "-f", "inet", "addr", "show"])
        if r.returncode != 0:
            return ""
    except Exception:
        return ""
    for ln in r.stdout.splitlines():
        f = ln.split()
        if len(f) < 4 or f[1] == "lo" or f[1].startswith("tailscale"):
            continue
        try:
            eige = ipaddress.ip_interface(f[3]).network
        except Exception:
            continue
        if maal.overlaps(eige) and f[1] == macvlan_dev():
            return (f"{maal} overlappar med nettet containeren alt står på "
                    f"({eige} på {f[1]}). Instrumentet må flyttast til eit "
                    f"anna subnett — elles kan trafikken ikkje rutast.")
    return ""


# ---------------------------------------------------------------
#  Ruter
# ---------------------------------------------------------------
def gjeldande_ruter() -> list:
    """Ruter som peikar ut bridge-grensesnittet."""
    bru = bru_grensesnitt()
    if not bru:
        return []
    ut = []
    try:
        r = _kjoer(["ip", "route", "show"])
        for ln in r.stdout.splitlines():
            f = ln.split()
            if not f or f[0] == "default":
                continue
            if "dev" in f and f[f.index("dev") + 1] == bru["dev"] and "via" in f:
                ut.append(f[0])
    except Exception:
        pass
    return ut


def bruk_ruter(nett: list = None) -> list:
    """Legg inn ruter for instrumentnetta. Returnerer liste med resultat.

    Idempotent: `ip route replace` gjer at gjentatte kall ikkje feilar.
    """
    konfig = les_konfig()
    if nett is None:
        nett = konfig["nett"]
    if not konfig["aktivert"]:
        return [{"subnett": n["subnett"], "ok": False,
                 "melding": "Instrument-ruting er slått av"} for n in nett]

    bru = bru_grensesnitt()
    if not bru:
        return [{"subnett": n["subnett"], "ok": False,
                 "melding": ("Containeren har ikkje noko bridge-nett. Legg til "
                             "instrumentnett-nettverket i docker-compose.yml og "
                             "køyr «docker compose up -d» på verten.")}
                for n in nett]

    ut = []
    for n in nett:
        subnett = n["subnett"]
        kol = kolliderer(subnett)
        if kol:
            ut.append({"subnett": subnett, "ok": False, "melding": kol})
            continue
        try:
            r = _kjoer(["ip", "route", "replace", subnett,
                        "via", bru["gateway"], "dev", bru["dev"]])
        except Exception as e:
            ut.append({"subnett": subnett, "ok": False, "melding": str(e)})
            continue
        if r.returncode == 0:
            log.info(f"Rute lagt inn: {subnett} via {bru['gateway']} "
                     f"dev {bru['dev']}")
            ut.append({"subnett": subnett, "ok": True,
                       "melding": f"via {bru['gateway']} ({bru['dev']})"})
        else:
            feil = (r.stderr or r.stdout or "").strip()
            ut.append({"subnett": subnett, "ok": False, "melding": feil})
    return ut


def bruk_frå_konfig() -> None:
    """Kallast ved oppstart. Ruter i containeren overlever ikkje restart,
    so dei må leggjast inn på nytt kvar gong."""
    sikre_default_rute()
    konfig = les_konfig()
    if not konfig["nett"]:
        return
    for res in bruk_ruter():
        if res["ok"]:
            log.info(f"Instrumentnett {res['subnett']}: {res['melding']}")
        else:
            log.warning(f"Instrumentnett {res['subnett']}: {res['melding']}")


# ---------------------------------------------------------------
#  Status + test
# ---------------------------------------------------------------
def status() -> dict:
    bru = bru_grensesnitt()
    konfig = les_konfig()
    return {
        "aktivert": konfig["aktivert"],
        "nett": konfig["nett"],
        "bru": bru,
        "bru_tilgjengeleg": bool(bru),
        "aktive_ruter": gjeldande_ruter(),
        "melding": ("" if bru else
                    "Containeren manglar bridge-nettet. Krev éin gong "
                    "«docker compose up -d» på verten etter at "
                    "instrumentnett-nettverket er lagt inn i compose."),
    }


def test_naa(host: str, port: int = 80, timeout: float = 4.0) -> dict:
    """Prøv å nå eit instrument. Svarar på om ruta faktisk verkar."""
    import socket
    host = (host or "").strip()
    if not host:
        return {"ok": False, "melding": "Manglar adresse"}
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return {"ok": True, "melding": f"{host}:{port} svarar"}
    except Exception as e:
        return {"ok": False,
                "melding": f"{host}:{port} — {type(e).__name__}: {e}"}
    finally:
        try:
            s.close()
        except Exception:
            pass
