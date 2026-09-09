#!/usr/bin/env python3
"""
Instrument-NAT — la noden vere ruter mellom to nett som ikkje veit om kvarandre
==============================================================================
Elspec BLACKBOX (og mange andre instrument) er fastlaast paa 192.168.1.0/24
paa innsida, og gaar tilbake dit ved reset. Det er som regel same subnett
som kunde-LAN-et noden staar paa. To grensesnitt paa same subnett gir
tvitydig ruting, og med instrumentet paa .1 - same adresse som LAN-gatewayen
- finst det inga maske som reddar det.

Loesinga er aa ikkje la dei moetast i det heile. Noden blir ein ruter:

    LAN-verda                  |  Instrument-verda
    192.168.1.0/24 paa end0    |  192.168.1.0/24 paa wlan0
    hovud-rutingbord           |  eige rutingbord (t.d. 99)
                               |
        containeren snakkar berre med ALIAS-nettet:
        10.99.0.0/24  <--NETMAP 1:1-->  192.168.1.0/24
        10.99.0.1  =  instrumentet
        10.99.0.254 =  instrument-ruteren

Tre grep gjer det:

1. `nmcli ... ipv4.route-table <N>` legg ALLE rutene frae wifi-profilen i eit
   eige bord. Vertens hovudbord ser aldri instrumentnettet, so kollisjonen
   oppstaar aldri. Dette er kjernen - utan det hjelper ingenting anna.
2. `iptables -t mangle PREROUTING -d <alias> -j MARK` merkjer trafikken, og
   `ip rule fwmark <N> lookup <N>` sender berre den ut wifi-bordet. Merket
   blir sett FOER nat-PREROUTING, so rutevalet etter NETMAP brukar rett bord.
3. `iptables -t nat PREROUTING -d <alias> -j NETMAP --to <ekte>` mapper heile
   subnettet 1:1, og MASQUERADE ut wifi-grensesnittet gir instrumentet ei
   avsendaradresse det kan svare til.

Alt koeyrer paa VERTEN via `nsenter -t 1` (same moenster som wifi_manager),
og alt er idempotent: reglar blir sjekka foer dei blir lagt til, so gjentatte
kall og restartar ikkje hopar opp duplikat.
"""

import ipaddress
import json
import logging
import os
import subprocess

log = logging.getLogger("instrument_nat")

KONFIG_FIL = "/data/konfig/instrument_nat.json"

# Hald oss unna bord/merke som andre kan bruke.
FOERSTE_TABELL = 99


# ---------------------------------------------------------------
#  Kommandoar paa verten
# ---------------------------------------------------------------
_HOST_NS = ["nsenter", "-t", "1", "-m", "-u", "-n", "-i"]


def _host(cmd: list, timeout: float = 20.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(_HOST_NS + cmd, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)


def _ok(r) -> bool:
    return r is not None and r.returncode == 0


# ---------------------------------------------------------------
#  Konfig
# ---------------------------------------------------------------
def _standard_nett(i: int) -> dict:
    return {
        "namn": "",
        "grensesnitt": "wlan0",
        "ekte": "192.168.1.0/24",
        "alias": f"10.{99 + i}.0.0/24",
        "tabell": FOERSTE_TABELL + i,
        "merke": FOERSTE_TABELL + i,
    }


def les_konfig() -> dict:
    try:
        with open(KONFIG_FIL, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {"aktivert": True, "nett": []}
    nett = []
    for i, n in enumerate(d.get("nett") or []):
        base = _standard_nett(i)
        base.update({k: v for k, v in n.items() if v not in (None, "")})
        base["tabell"] = int(base["tabell"])
        base["merke"] = int(base["merke"])
        nett.append(normaliser(base))
    return {"aktivert": bool(d.get("aktivert", True)), "nett": nett}


def normaliser(nett: dict) -> dict:
    """Gjer vertsadresser om til nettadresser.

    Ein skriv gjerne inn `192.168.1.1/24` fordi det er adressa ein tenkjer
    paa. iptables normaliserer sjoelv, men da samanliknar statussjekken raa
    streng mot iptables si normaliserte utskrift og finn aldri treff - so
    oppsettet ser inaktivt ut sjoelv naar regelen ligg inne.
    """
    ut = dict(nett)
    for k in ("ekte", "alias"):
        try:
            ut[k] = str(ipaddress.ip_network(ut.get(k, ""), strict=False))
        except Exception:
            pass
    return ut


def valider(nett: dict) -> str:
    """Tom streng om oppsettet er brukbart, elles forklaring."""
    try:
        ekte = ipaddress.ip_network(nett["ekte"], strict=False)
        alias = ipaddress.ip_network(nett["alias"], strict=False)
    except Exception as e:
        return f"Invalid subnet: {e}"
    if ekte.prefixlen != alias.prefixlen:
        return (f"Alias {alias} and instrument subnet {ekte} must have the "
                f"same prefix length - NETMAP maps 1:1.")
    if alias.overlaps(ekte):
        return (f"Alias {alias} overlaps the instrument subnet {ekte}. "
                f"Pick an alias range that exists nowhere else, e.g. "
                f"10.99.0.0/24.")
    if not nett.get("grensesnitt"):
        return "Interface is required (e.g. wlan0)"
    return ""


def lagre_konfig(konfig: dict) -> tuple:
    nett = []
    for i, n in enumerate(konfig.get("nett") or []):
        base = _standard_nett(i)
        base.update({k: v for k, v in n.items() if v not in (None, "")})
        base["tabell"] = int(base["tabell"])
        base["merke"] = int(base["merke"])
        base = normaliser(base)
        feil = valider(base)
        if feil:
            return False, feil
        nett.append(base)
    ut = {"aktivert": bool(konfig.get("aktivert", True)), "nett": nett}
    try:
        os.makedirs(os.path.dirname(KONFIG_FIL), exist_ok=True)
        with open(KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(ut, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return False, f"Could not save: {e}"
    return True, f"Saved {len(nett)} instrument NAT mapping(s)"


# ---------------------------------------------------------------
#  Oppsett paa verten
# ---------------------------------------------------------------
def _aktiv_profil(dev: str) -> str:
    r = _host(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", dev])
    if not _ok(r):
        return ""
    for ln in r.stdout.splitlines():
        k, _, v = ln.partition(":")
        if k == "GENERAL.CONNECTION" and v.strip() and v.strip() != "--":
            return v.strip()
    return ""


def _isoler_profil(dev: str, tabell: int) -> str:
    """Legg wifi-profilen sine ruter i eit eige bord.

    Dette er kjernen: utan det installerer NetworkManager 192.168.1.0/24 i
    hovudbordet, ved sida av LAN-et, og då er kollisjonen eit faktum før vi
    har fått gjort noko som helst.
    """
    profil = _aktiv_profil(dev)
    if not profil:
        return f"No active connection profile on {dev}"
    r = _host(["nmcli", "connection", "modify", profil,
               "ipv4.route-table", str(tabell),
               "ipv6.route-table", str(tabell),
               "ipv4.never-default", "yes",
               "ipv4.ignore-auto-dns", "yes",
               "ipv6.never-default", "yes"], timeout=25)
    if not _ok(r):
        return (r.stderr or r.stdout or "").strip()
    r = _host(["nmcli", "connection", "up", profil, "ifname", dev], timeout=60)
    if not _ok(r):
        return (r.stderr or r.stdout or "").strip()
    return ""


def _har_regel(tabell: int, merke: int) -> bool:
    r = _host(["ip", "rule", "show"])
    if not _ok(r):
        return False
    naal = f"lookup {tabell}"
    for ln in r.stdout.splitlines():
        if naal in ln and (f"fwmark {merke:#x}" in ln or f"fwmark {merke}" in ln):
            return True
    return False


def _iptables(tabell: str, kjede: str, regel: list, dev_sjekk=True) -> str:
    """Legg til ein iptables-regel om han ikkje finst frå før."""
    sjekk = _host(["iptables", "-t", tabell, "-C", kjede] + regel)
    if _ok(sjekk):
        return ""                      # finst alt
    r = _host(["iptables", "-t", tabell, "-A", kjede] + regel)
    if _ok(r):
        return ""
    return (r.stderr or r.stdout or "").strip()


def sett_opp(nett: dict) -> dict:
    """Set opp NAT-ruting for eitt instrumentnett. Returnerer resultat-dict."""
    feil = valider(nett)
    if feil:
        return {"namn": nett.get("namn", ""), "ok": False, "melding": feil}

    nett = normaliser(nett)
    dev = nett["grensesnitt"]
    ekte, alias = nett["ekte"], nett["alias"]
    tabell, merke = int(nett["tabell"]), int(nett["merke"])
    steg = []

    # 1. Isoler wifi-rutene i eige bord
    m = _isoler_profil(dev, tabell)
    if m:
        return {"namn": nett.get("namn", ""), "ok": False,
                "melding": f"Could not isolate {dev}: {m}"}
    steg.append(f"{dev}-ruter i bord {tabell}")

    # 2. Sørg for at instrumentnettet finst i bordet (NM legg det normalt inn
    #    sjølv, men ikkje om adressa er /32 eller profilen er spesiell)
    _host(["ip", "route", "replace", ekte, "dev", dev, "table", str(tabell)])

    # 3. Regel: merka trafikk brukar det bordet
    if not _har_regel(tabell, merke):
        r = _host(["ip", "rule", "add", "fwmark", str(merke),
                   "lookup", str(tabell)])
        if not _ok(r):
            return {"namn": nett.get("namn", ""), "ok": False,
                    "melding": f"ip rule failed: "
                               f"{(r.stderr or r.stdout or '').strip()}"}
    steg.append(f"fwmark {merke} → bord {tabell}")

    # 4. Merk trafikk mot aliaset. Må skje i mangle, som køyrer FØR nat —
    #    elles er destinasjonen alt omskriven når vi vil kjenne han att.
    m = _iptables("mangle", "PREROUTING",
                  ["-d", alias, "-j", "MARK", "--set-mark", str(merke)])
    if m:
        return {"namn": nett.get("namn", ""), "ok": False,
                "melding": f"mangle rule failed: {m}"}

    # 5. NETMAP: heile aliasnettet 1:1 over på det ekte
    m = _iptables("nat", "PREROUTING",
                  ["-d", alias, "-j", "NETMAP", "--to", ekte])
    if m:
        return {"namn": nett.get("namn", ""), "ok": False,
                "melding": f"NETMAP failed: {m}"}
    steg.append(f"{alias} ⇄ {ekte}")

    # 6. MASQUERADE ut instrument-grensesnittet, så instrumentet svarar til
    #    ei adresse på sitt eige nett
    m = _iptables("nat", "POSTROUTING", ["-o", dev, "-j", "MASQUERADE"])
    if m:
        return {"namn": nett.get("namn", ""), "ok": False,
                "melding": f"MASQUERADE failed: {m}"}

    # 7. Laus reverse-path-sjekk: med to like subnett i ulike bord vil streng
    #    rp_filter kaste svara.
    _host(["sysctl", "-w", "net.ipv4.conf.all.rp_filter=2"])
    _host(["sysctl", "-w", f"net.ipv4.conf.{dev}.rp_filter=2"])
    _host(["sysctl", "-w", "net.ipv4.ip_forward=1"])

    log.info(f"Instrument-NAT oppe: {alias} → {ekte} via {dev} (bord {tabell})")
    # NAT-en paa verten er berre halve vegen: containeren maa ogsaa ha rute
    # til alias-nettet, og den gaar via bridge-nettet. Manglar det, blir
    # ingenting naabart - og da skal vi seie det, ikkje melde suksess.
    mangel = ""
    try:
        import instrument_ruter
        if not instrument_ruter.bru_grensesnitt():
            mangel = (" - but the container has no bridge network yet, so "
                      "nothing can reach it. Rebuild the container first.")
    except Exception:
        pass
    return {"namn": nett.get("namn", ""), "ok": not mangel,
            "alias": alias, "ekte": ekte,
            "melding": ", ".join(steg) + mangel}


def riv_ned(nett: dict) -> dict:
    """Fjern oppsettet for eitt instrumentnett."""
    dev = nett["grensesnitt"]
    alias, ekte = nett["alias"], nett["ekte"]
    merke, tabell = int(nett["merke"]), int(nett["tabell"])
    _host(["iptables", "-t", "nat", "-D", "PREROUTING",
           "-d", alias, "-j", "NETMAP", "--to", ekte])
    _host(["iptables", "-t", "mangle", "-D", "PREROUTING",
           "-d", alias, "-j", "MARK", "--set-mark", str(merke)])
    _host(["ip", "rule", "del", "fwmark", str(merke), "lookup", str(tabell)])
    return {"namn": nett.get("namn", ""), "ok": True, "melding": "Removed"}


def bruk_frå_konfig() -> list:
    """Kallast ved oppstart — reglar på verten overlever ikkje reboot."""
    konfig = les_konfig()
    if not konfig["aktivert"] or not konfig["nett"]:
        return []
    ut = []
    for n in konfig["nett"]:
        try:
            ut.append(sett_opp(n))
        except Exception as e:
            ut.append({"namn": n.get("namn", ""), "ok": False,
                       "melding": str(e)})
    return ut


# ---------------------------------------------------------------
#  Status
# ---------------------------------------------------------------
def _foerste_vert(subnett: str) -> str:
    try:
        return str(next(ipaddress.ip_network(subnett, strict=False).hosts()))
    except Exception:
        return ""


def status() -> dict:
    konfig = les_konfig()
    ut = {"aktivert": konfig["aktivert"], "nett": [], "vert_ok": False}

    r = _host(["ip", "rule", "show"])
    reglar = r.stdout if _ok(r) else ""
    ut["vert_ok"] = _ok(r)

    r = _host(["iptables", "-t", "nat", "-S", "PREROUTING"])
    natreglar = r.stdout if _ok(r) else ""
    r = _host(["iptables", "-t", "mangle", "-S", "PREROUTING"])
    mangle = r.stdout if _ok(r) else ""
    # Teljarar: ein regel som finst men aldri blir treft er like ubrukeleg
    # som ein som manglar - og det er einaste maaten aa sjaa skilnaden.
    r = _host(["iptables", "-t", "mangle", "-L", "PREROUTING", "-v", "-n", "-x"])
    ut["mangle_teljarar"] = (r.stdout or "").strip().splitlines() if _ok(r) else []
    r = _host(["iptables", "-t", "nat", "-L", "PREROUTING", "-v", "-n", "-x"])
    ut["nat_teljarar"] = (r.stdout or "").strip().splitlines() if _ok(r) else []
    r = _host(["ip", "rule", "show"])
    ut["ip_rule"] = (r.stdout or "").strip().splitlines() if _ok(r) else []

    for n in konfig["nett"]:
        n = normaliser(n)
        har_regel = f"lookup {n['tabell']}" in reglar
        har_netmap = n["alias"] in natreglar
        # Merkinga er like naudsynt som dei to andre. Utan henne blir
        # pakkene omsette og so rutte ut FEIL grensesnitt - dei naar LAN-et
        # i staden for instrumentnettet, og alt ser vellukka ut.
        har_merke = (n["alias"] in mangle
                     and ("MARK" in mangle or "mark" in mangle))
        r2 = _host(["ip", "route", "show", "table", str(n["tabell"])])
        bord = r2.stdout.strip().splitlines() if _ok(r2) else []

        # Kjernen sitt eige svar paa kva veg pakken tek. Dette er fasiten.
        maal = _foerste_vert(n["ekte"])
        rute_med, rute_utan = "", ""
        if maal:
            rm = _host(["ip", "route", "get", maal, "mark", str(n["merke"])])
            if _ok(rm):
                rute_med = (rm.stdout or "").strip().splitlines()[:1]
                rute_med = rute_med[0] if rute_med else ""
            ru = _host(["ip", "route", "get", maal])
            if _ok(ru):
                rute_utan = (ru.stdout or "").strip().splitlines()[:1]
                rute_utan = rute_utan[0] if rute_utan else ""

        rett_veg = bool(rute_med) and f"dev {n['grensesnitt']}" in rute_med
        ut["nett"].append({
            **n,
            "aktiv": har_regel and har_netmap and har_merke and rett_veg,
            "har_ip_rule": har_regel,
            "har_netmap": har_netmap,
            "har_merke": har_merke,
            "rett_veg": rett_veg,
            "rute_med_merke": rute_med,
            "rute_utan_merke": rute_utan,
            "bord": bord,
        })
    return ut


def test_naa(alias_ip: str, port: int = 80, timeout: float = 4.0) -> dict:
    """Prøv å nå instrumentet på alias-adressa, frå containeren."""
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((alias_ip, int(port)))
        return {"ok": True, "melding": f"{alias_ip}:{port} responds"}
    except Exception as e:
        return {"ok": False,
                "melding": f"{alias_ip}:{port} — {type(e).__name__}: {e}"}
    finally:
        try:
            s.close()
        except Exception:
            pass
