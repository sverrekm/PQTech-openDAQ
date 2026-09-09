#!/usr/bin/env python3
"""
Nett-skann — sjaa kva som faktisk staar paa eit instrumentnett
==============================================================
Naar ein node faar rute til eit instrumentnett (sjaa instrument_ruter.py)
er neste spoersmaal alltid det same: kva finst der, og kva snakkar dei?
Utan svar paa det blir Modbus-oppsett gjetting.

Skannar eit subnett frae CONTAINEREN, altsaa gjennom same rute som
maalepollinga sjoelv vil bruke. Det er poenget: svarar eit instrument her,
vil openDAQ-brua ogsaa naa det. Eit skann frae ein PC paa eit anna nett
ville ikkje sagt det same.

Metode:
  1. Oppdaging - TCP-connect mot nokre faa vanlege portar, pluss ICMP
     echo naar raw socket er lov (containeren har NET_RAW).
  2. Portskann - full portliste, men berre mot vertar som svarte.
  3. Kjenneteikn - HTTP-banner (Server-header + <title>) frae dei som har
     ein webserver. Det er som regel nok til aa skilje ein Elspec
     (GoAhead) frae ein PQube, ein switch eller ein ruter.

Berre standardbiblioteket. Skannet koeyrer i bakgrunnstraad og kan
stoppast, sidan eit /24 tek titals sekund - lenger enn hub-proxyen sin
lesetimeout paa 30 s.
"""

import ipaddress
import logging
import re
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("nett_skann")

# Portar som seier noko om eit maaleinstrument. (nummer, namn)
PORTAR = [
    (21, "FTP"), (22, "SSH"), (23, "Telnet"), (80, "HTTP"),
    (102, "IEC 61850/MMS"), (443, "HTTPS"), (502, "Modbus TCP"),
    (1883, "MQTT"), (2404, "IEC 60870-5-104"), (4840, "OPC UA"),
    (7420, "openDAQ streaming"), (8080, "HTTP (alt)"), (8443, "HTTPS (alt)"),
    (9100, "JetDirect"), (20000, "DNP3"),
]

# Portar som betyr at vi faktisk kan hente MAALEDATA herifraa, eller styre
# eininga. Dei blir loefta fram i GUI-et; resten er berre kontekst.
INTERESSANTE = {502, 4840, 7420, 1883, 102, 20000, 2404, 22}

# OUI -> produsent. Ei eining som ikkje svarar paa nokon port vi proever er
# usynleg for eit portskann, men han svarar paa ARP - og da fortel MAC-en
# kven han er. Vi held lista kort og relevant for maaleutstyr.
OUI = {
    "00:60:35": "Elspec",
    "00:0c:29": "VMware", "00:50:56": "VMware",
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi",
    "e4:5f:01": "Raspberry Pi", "d8:3a:dd": "Raspberry Pi",
    "00:1c:23": "Dell", "00:26:b9": "Dell",
    "00:04:a3": "Microchip", "00:80:a3": "Lantronix",
    "00:90:e8": "Moxa", "00:0e:8e": "SparkLAN",
    "00:11:32": "Synology", "00:1d:73": "Buffalo",
    "74:fe:48": "Ginlong/Solis",
}

# Mindre sett for oppdagingsfasen — held skannet raskt.
OPPDAGING = [80, 502, 443, 22, 4840, 8080, 23, 21]

MAKS_VERTAR = 1024          # /22. Større skann er nesten alltid ein tastefeil

# Kor lenge vi ventar per port. For kort, og treige einingar (eller eit
# nett med kald ARP-cache) svarar ikkje i tide - da ser eit fullt nett
# tomt ut. Justerbar per skann.
STANDARD_TIMEOUT = 1.2

# Parallellitet. 64 druknar nabotabellen paa eit macvlan-grensesnitt:
# eit /24 er 254 ARP-oppslag der dei fleste aldri blir svara paa, og da
# blir dei gyldige oppfoeringane kasta ut. Da fann vi NULL vertar paa eit
# nett med tjue. Lavare tal er tregare, men finn faktisk noko.
STANDARD_TRAADAR = 16
BOLK = 32                    # adresser per bolk i oppdagingsfasen
BOLK_PUST = 0.3              # sekund mellom bolkar

_lock = threading.Lock()
_stopp = threading.Event()
_tilstand = {
    "tilstand": "",         # koeyrer | ferdig | stoppa | feil
    "subnett": "",
    "grensesnitt": "",
    "ferdig": 0,
    "totalt": 0,
    "funn": [],
    "melding": "",
    "starta": None,
    "brukt_s": None,
}


def status() -> dict:
    with _lock:
        d = dict(_tilstand)
        d["funn"] = list(d["funn"])
    return d


def _sett(**kv) -> None:
    with _lock:
        _tilstand.update(kv)


# ---------------------------------------------------------------
#  Probar
# ---------------------------------------------------------------
# Grensesnittet skannet skal gaa ut. Tomt = la rutinga velje.
_bind_dev = ""


def _bind(s) -> None:
    """Bind ein socket til eit gjeve grensesnitt.

    Naar to nettverk har same subnett - og det er heile grunnen til at
    instrument-NAT finst - kan ikkje rutinga aleine avgjere kva nett vi
    meiner. Da maa vi seie det eksplisitt. Krev NET_RAW, som containeren
    har; feilar det, skannar vi via rutinga i staden for aa gi opp.
    """
    if not _bind_dev:
        return
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                     _bind_dev.encode() + b"\0")
    except Exception:
        pass


def _tcp(ip: str, port: int, timeout: float) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    _bind(s)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _icmp(ip: str, timeout: float = 1.0) -> bool:
    """ICMP echo via raw socket. Krev NET_RAW; returnerer False om ikkje lov.

    Verdt aa ha med: mange instrument svarar paa ping men held alle
    TCP-portar stengde til dei blir konfigurerte.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    except Exception:
        return False
    try:
        s.settimeout(timeout)
        _bind(s)
        ident = threading.get_ident() & 0xFFFF
        hode = struct.pack("!BBHHH", 8, 0, 0, ident, 1)
        data = b"pqtech-skann"
        sjekk = _sjekksum(hode + data)
        pakke = struct.pack("!BBHHH", 8, 0, sjekk, ident, 1) + data
        s.sendto(pakke, (ip, 0))
        slutt = time.time() + timeout
        while time.time() < slutt:
            s.settimeout(max(0.05, slutt - time.time()))
            svar, adr = s.recvfrom(1024)
            if adr[0] == ip and len(svar) >= 28 and svar[20] == 0:
                return True
        return False
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _sjekksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    sum_ = 0
    for i in range(0, len(data), 2):
        sum_ += (data[i] << 8) + data[i + 1]
    sum_ = (sum_ >> 16) + (sum_ & 0xFFFF)
    sum_ += sum_ >> 16
    return ~sum_ & 0xFFFF


_TITTEL = re.compile(rb"<title[^>]*>(.{0,120}?)</title>", re.I | re.S)
_SERVER = re.compile(rb"^Server:\s*(.{0,80})$", re.I | re.M)


def _http_banner(ip: str, port: int, timeout: float = 3.0) -> dict:
    """Server-header og <title>. Nok til å kjenne att dei fleste instrument."""
    ut = {}
    s = socket.socket()
    s.settimeout(timeout)
    _bind(s)
    try:
        s.connect((ip, port))
        s.sendall(f"GET / HTTP/1.1\r\nHost: {ip}\r\n"
                  f"User-Agent: PQTech-skann\r\nConnection: close\r\n\r\n"
                  .encode())
        buf = b""
        while len(buf) < 8192:
            bit = s.recv(4096)
            if not bit:
                break
            buf += bit
    except Exception:
        return ut
    finally:
        try:
            s.close()
        except Exception:
            pass
    m = _SERVER.search(buf)
    if m:
        ut["server"] = m.group(1).decode("utf-8", "replace").strip()
    m = _TITTEL.search(buf)
    if m:
        tittel = m.group(1).decode("utf-8", "replace")
        ut["tittel"] = " ".join(tittel.split())[:80]
    return ut


# ---------------------------------------------------------------
#  Skann
# ---------------------------------------------------------------
def _skann_vert(ip: str, timeout: float) -> dict:
    """Oppdaging for éin vert. Returnerer {} om han ikkje svarar."""
    treff = [p for p in OPPDAGING if not _stopp.is_set() and _tcp(ip, p, timeout)]
    if not treff and not _stopp.is_set():
        if not _icmp(ip, timeout):
            return {}
        return {"ip": ip, "portar": [], "svar": "ICMP"}
    return {"ip": ip, "portar": treff, "svar": "TCP"}


def _detaljer(funn: dict, timeout: float) -> dict:
    """Full portliste + HTTP-banner for ein vert som alt har svart."""
    ip = funn["ip"]
    opne = []
    for port, namn in PORTAR:
        if _stopp.is_set():
            break
        if port in funn["portar"] or _tcp(ip, port, timeout):
            opne.append({"port": port, "namn": namn,
                         "interessant": port in INTERESSANTE})
    funn["portar"] = opne
    for p in (80, 8080, 443, 8443):
        if _stopp.is_set():
            break
        if any(o["port"] == p for o in opne):
            banner = _http_banner(ip, p)
            if banner:
                funn.update(banner)
                break
    return funn


def _kjoer(nett, timeout: float, traadar: int) -> None:
    start = time.time()
    vertar = list(nett.hosts()) or [nett.network_address]
    _sett(tilstand="koeyrer", totalt=len(vertar), ferdig=0, funn=[],
          melding="", starta=start, brukt_s=None)
    try:
        with ThreadPoolExecutor(max_workers=traadar) as pool:
            oppdaga = []
            # Bolkvis: heile lista paa ein gong druknar nabotabellen.
            for i in range(0, len(vertar), BOLK):
                if _stopp.is_set():
                    break
                bolk = vertar[i:i + BOLK]
                for res in pool.map(lambda h: _skann_vert(str(h), timeout),
                                    bolk):
                    with _lock:
                        _tilstand["ferdig"] += 1
                    if res:
                        oppdaga.append(res)
                        # Vis treff etter kvart, so brukaren ser framdrift
                        with _lock:
                            _tilstand["funn"] = sorted(
                                oppdaga + [], key=lambda f: tuple(
                                    int(x) for x in f["ip"].split(".")))
                    if _stopp.is_set():
                        break
                time.sleep(BOLK_PUST)

            if not _stopp.is_set():
                _sett(melding=f"Found {len(oppdaga)} hosts - reading ports")
                ferdige = list(pool.map(lambda f: _detaljer(f, timeout), oppdaga))
                # ARP-tabellen er fersk etter skannet
                mac = _mac_tabell()
                for f in ferdige:
                    m = mac.get(f["ip"], "")
                    if m:
                        f["mac"] = m
                        p = _produsent(m)
                        if p:
                            f["produsent"] = p
                with _lock:
                    _tilstand["funn"] = sorted(
                        ferdige, key=lambda f: tuple(
                            int(x) for x in f["ip"].split(".")))
    except Exception as e:
        _sett(tilstand="feil", melding=str(e),
              brukt_s=round(time.time() - start, 1))
        return

    brukt = round(time.time() - start, 1)
    if _stopp.is_set():
        _sett(tilstand="stoppa", melding="Scan stopped", brukt_s=brukt)
    else:
        n = len(status()["funn"])
        _lagre_resultat(str(nett), status()["funn"])
        _sett(tilstand="ferdig", brukt_s=brukt,
              melding=f"{n} {'device' if n == 1 else 'devices'} found in "
                      f"{brukt:.0f} s")


# --- Lager: siste resultat per subnett -------------------------------
# GUI-et skal kunne vise kva som staar paa instrumentnettet utan at nokon
# maa trykkje "skann" foerst.
_siste = {}

# Lagra paa disk: resultata laag berre i minnet, so kvar restart (og kvar
# oppdatering) tomte lista. Da forsvann bade oversikta og "Opne web-GUI"-
# lenkjene til neste autoskann - opptil ein halvtime seinare.
LAGER_FIL = "/data/konfig/nettskann.json"


def _last_frå_disk() -> None:
    import json
    try:
        with open(LAGER_FIL, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return
    if not isinstance(d, dict):
        return
    with _lock:
        for sub, res in d.items():
            if isinstance(res, dict) and isinstance(res.get("funn"), list):
                _siste[sub] = {"tid": float(res.get("tid") or 0),
                               "funn": res["funn"]}


def _skriv_til_disk() -> None:
    import json, os
    try:
        os.makedirs(os.path.dirname(LAGER_FIL), exist_ok=True)
        with _lock:
            d = {k: dict(v) for k, v in _siste.items()}
        with open(LAGER_FIL, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def siste() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _siste.items()}


def _lagre_resultat(subnett: str, funn: list) -> None:
    with _lock:
        _siste[subnett] = {"tid": time.time(), "funn": list(funn)}
    _skriv_til_disk()


def _auto_loop(hent_subnett, intervall_min: float) -> None:
    """Skannar instrumentnetta jamt. Instrument kjem og gaar - ein node som
    staar i eit anlegg i maanader skal vise kva som ER der, ikkje kva som
    var der da nokon sist trykte paa ein knapp."""
    # Vent litt so rutene rekk aa komme opp etter oppstart
    time.sleep(45)
    while True:
        try:
            for subnett in (hent_subnett() or []):
                with _lock:
                    travel = _tilstand["tilstand"] == "koeyrer"
                if travel:
                    break                      # manuelt skann har forrang
                ok, _ = start(subnett)
                if not ok:
                    continue
                while status()["tilstand"] == "koeyrer":
                    time.sleep(1)
                st = status()
                if st["tilstand"] == "ferdig":
                    _lagre_resultat(subnett, st["funn"])
                    log.info(f"Autoskann {subnett}: {len(st['funn'])} vertar")
        except Exception as e:
            log.warning(f"Autoskann feila: {e}")
        time.sleep(max(60.0, intervall_min * 60.0))


def start_auto(hent_subnett, intervall_min: float = 30.0) -> None:
    """Start bakgrunns-autoskann, og hent fram det vi fann sist."""
    _last_frå_disk()
    return _start_auto(hent_subnett, intervall_min)


def _start_auto(hent_subnett, intervall_min: float = 30.0) -> None:
    """Start bakgrunns-autoskann. `hent_subnett` er ein callable som gir
    lista over subnett som skal skannast (typisk alias-netta)."""
    if intervall_min <= 0:
        return
    threading.Thread(target=_auto_loop, args=(hent_subnett, intervall_min),
                     daemon=True, name="nett-autoskann").start()
    log.info(f"Autoskann av instrumentnett kvart {intervall_min:.0f} min")


def start(subnett: str, timeout: float = STANDARD_TIMEOUT,
          traadar: int = STANDARD_TRAADAR,
          grensesnitt: str = "") -> tuple:
    """Start eit skann i bakgrunnen. Returnerer (ok, melding).

    `grensesnitt` bind skannet til eit av containeren sine eigne
    grensesnitt. Tomt = foelg rutinga, som er rett i dei fleste tilfelle.
    """
    global _bind_dev
    try:
        nett = ipaddress.ip_network(str(subnett).strip(), strict=False)
    except Exception as e:
        return False, f"Invalid subnet: {e}"
    if nett.num_addresses > MAKS_VERTAR:
        return False, (f"{nett} has {nett.num_addresses} addresses. The limit "
                       f"is {MAKS_VERTAR} - scan a smaller range.")
    with _lock:
        if _tilstand["tilstand"] == "koeyrer":
            return False, f"A scan of {_tilstand['subnett']} is already running."
    # Eit skann utan rute gir "0 einingar", som ser ut som eit tomt nett.
    # Det er noko heilt anna enn at vi ikkje kom oss ut, og skilnaden er
    # akkurat den brukaren treng for aa vite kva han skal gjere.
    mangel = _manglar_rute(str(nett))
    if mangel:
        return False, mangel

    _stopp.clear()
    _bind_dev = (grensesnitt or "").strip()
    _sett(subnett=str(nett), grensesnitt=_bind_dev)
    threading.Thread(target=_kjoer, args=(nett, timeout, traadar),
                     daemon=True, name="nett-skann").start()
    return True, f"Scanning {nett} ..."


def _mac_tabell() -> dict:
    """{ip: mac} frae ARP-tabellen, baade i containeren og paa verten.

    Containeren ser berre det som er naabart paa sitt eige L2. Nett som
    ligg bak verten - t.d. wifi-sida - finst berre i vertens tabell.
    """
    import subprocess
    ut = {}
    for cmd in (["ip", "neigh", "show"],
                ["nsenter", "-t", "1", "-m", "-u", "-n", "-i",
                 "ip", "neigh", "show"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for ln in (r.stdout or "").splitlines():
            f = ln.split()
            if len(f) >= 5 and f[3] == "lladdr":
                ut.setdefault(f[0], f[4].lower())
    return ut


def naboar() -> list:
    """[{ip, mac, dev, produsent}] - ARP per GRENSESNITT.

    Same IP kan finnast paa fleire grensesnitt naar to nett deler subnett.
    Da er det MAC-en per grensesnitt som fortel kven som eigentleg svarar
    kvar - og det er einaste maaten aa skilje eit instrument frae ein ruter
    som begge kallar seg .1.
    """
    import subprocess
    ut, sett = [], set()
    for cmd in (["ip", "neigh", "show"],
                ["nsenter", "-t", "1", "-m", "-u", "-n", "-i",
                 "ip", "neigh", "show"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for ln in (r.stdout or "").splitlines():
            f = ln.split()
            if len(f) >= 5 and f[1] == "dev" and f[3] == "lladdr":
                nokkel = (f[0], f[2], f[4].lower())
                if nokkel in sett:
                    continue
                sett.add(nokkel)
                ut.append({"ip": f[0], "dev": f[2], "mac": f[4].lower(),
                           "produsent": _produsent(f[4]),
                           "tilstand": f[-1]})
    return sorted(ut, key=lambda d: (d["dev"], d["ip"]))


def _produsent(mac: str) -> str:
    return OUI.get((mac or "")[:8].lower(), "")


def _manglar_rute(subnett: str) -> str:
    """Tom streng om nettet er naabart, elles forklaring.

    Er nettet konfigurert som instrumentnett, men ikkje aktivt rutt, kjem
    pakkene aldri ut - da skal vi seie det i staden for aa rapportere eit
    tomt resultat.
    """
    try:
        import instrument_ruter as ir
        konfigurert = {n["subnett"] for n in
                       ir.les_konfig()["nett"] + ir.alias_nett()}
        if subnett not in konfigurert:
            return ""                       # eit vanleg nett - berre skann
        if subnett in set(ir.gjeldande_ruter()):
            return ""
        if not ir.bru_grensesnitt():
            return ("No route to %s: the container has no bridge network "
                    "yet. Rebuild the container first (step 1)." % subnett)
        return ("No route to %s. It is configured as an instrument network, "
                "but no route is active for it." % subnett)
    except Exception:
        return ""


def maal() -> list:
    """Nett det gir meining aa skanne, med grensesnittet dei ligg bak.

    Sett saman av containeren sine eigne grensesnitt og dei konfigurerte
    instrumentnetta, so brukaren slepp aa skrive subnett for hand - og
    slepp aa gjette kva alias som hoeyrer til kva instrument.
    """
    ut = []
    try:
        import instrument_ruter as ir
        mv = ir.macvlan_dev()
        for dev, cidr in ir.grensesnitt():
            try:
                nett = str(ipaddress.ip_interface(cidr).network)
            except Exception:
                continue
            ut.append({
                "namn": "Local network" if dev == mv else "Container bridge",
                "subnett": nett, "grensesnitt": dev, "kan_binde": True})
        for n in ir.les_konfig()["nett"] + ir.alias_nett():
            if not any(x["subnett"] == n["subnett"] for x in ut):
                ut.append({"namn": n.get("namn") or "Instrument network",
                           "subnett": n["subnett"], "grensesnitt": "",
                           "kan_binde": False})
    except Exception:
        pass
    return ut


def stopp() -> tuple:
    _stopp.set()
    return True, "Stopping the scan"
