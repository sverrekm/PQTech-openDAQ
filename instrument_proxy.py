#!/usr/bin/env python3
"""
Instrument-proxy — naa instrumentet sitt web-GUI gjennom hubben
===============================================================
Eit instrument som berre finst paa eit maalenett er utilgjengeleg for alle
som ikkje staar fysisk paa staden. Elspec BLACKBOX-en er eit doeme: han
ligg paa 192.168.1.1 bak wifi-et, naabar for noden som 10.99.0.1, men
usynleg for oss.

Noden er alt naabar gjennom hubben. Da kan han like godt vidareformidle:

    https://opendac.pqtech.no/node-proxy/<node>/instrument/10.99.0.1/

Hub-sesjonen gjeld foran, ingen portar blir opna mot internett, og det
verkar for kva som helst instrument paa kva som helst nodenett.

Det vanskelege er ikkje sjoelve vidareformidlinga, men ADRESSENE i svaret.
Eit instrument-GUI peikar paa /style.css og /cgi-bin/... - absolutte stiar
som ville hamna paa hubben sin rot i staden for hos instrumentet. Difor
skriv vi dei om til aa peike gjennom proxyen. Relative stiar klarar seg
sjoelv so lenge URL-en endar paa /.
"""

import ipaddress
import re

# Berre private adresser. Utan denne avgrensinga ville noden vore ein open
# proxy mot internett for alle med hub-tilgang.
PRIVATE = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
]

# Hop-by-hop-headerar skal ikkje vidareformidlast.
HOPP = {
    "content-length", "content-encoding", "transfer-encoding", "connection",
    "keep-alive", "upgrade", "proxy-authorization", "proxy-authenticate",
    "te", "trailers", "host",
}

# src="/x", href='/x', action="/x" - men ikkje //host (protokoll-relativ)
_ABSOLUTT = re.compile(rb"""(\s(?:src|href|action|data-src)\s*=\s*["'])/(?!/)""",
                       re.I)
_CSS_URL = re.compile(rb"""(url\(\s*["']?)/(?!/)""", re.I)


def tillat_vert(vert: str) -> bool:
    """Berre private adresser, og berre reine IP-ar.

    Vertsnamn ville krevd DNS-oppslag vi ikkje kan stole paa, og opna for
    aa bruke noden som mellomledd mot kva som helst.
    """
    try:
        adr = ipaddress.ip_address((vert or "").strip())
    except Exception:
        return False
    return any(adr in n for n in PRIVATE)


def prefiks(forwarded_prefix: str, vert: str, port: int = 80) -> str:
    """Stien alt i svaret skal peike gjennom.

    Naar hubben proxar oss, ser browseren /node-proxy/<id>/... - og da maa
    omskrivinga ta med det leddet, elles hamnar lenkjene paa hubben sin rot.
    """
    p = (forwarded_prefix or "").rstrip("/")
    havn = "" if port == 80 else f":{port}"
    return f"{p}/instrument/{vert}{havn}"


def _eigne_url(vert: str, havn: int) -> list:
    """Dei formene instrumentet kan skrive si eiga adresse paa."""
    ut = [f"http://{vert}:{havn}", f"//{vert}:{havn}"]
    if havn == 80:
        ut += [f"http://{vert}", f"//{vert}"]
    return ut


def skriv_om_html(kropp: bytes, pre: str, vert: str = "",
                  havn: int = 80) -> bytes:
    """Gjer adresser i svaret om til aa peike gjennom proxyen.

    Baade absolutte stiar (/style.css) og heile URL-ar med instrumentet si
    eiga adresse (http://10.99.0.1/login.asp). Det siste er vanleg i
    innebygde webserverar, og utan omskriving fell brukaren ut av proxyen
    og over paa ei adresse browseren hans ikkje naar.
    """
    if not kropp:
        return kropp
    b = pre.encode("utf-8")
    # REKKJEFOELGJA er viktig. Tek vi URL-ane foerst, blir resultatet
    # href="/<pre>/x" - og da matchar regexen for absolutte stiar det paa
    # nytt og legg prefikset inn ein gong til. Difor stiane foerst: dei
    # roerer ikkje "http://..."-formene, som so blir tekne etterpaa.
    kropp = _ABSOLUTT.sub(rb"\1" + b + b"/", kropp)
    kropp = _CSS_URL.sub(rb"\1" + b + b"/", kropp)
    if vert:
        for u in _eigne_url(vert, havn):
            kropp = kropp.replace(u.encode("utf-8"), b)
    return kropp


def skriv_om_location(verdi: str, pre: str, vert: str = "",
                      havn: int = 80) -> str:
    """Redirect innanfor instrumentet skal bli verande i proxyen."""
    if vert:
        for u in _eigne_url(vert, havn):
            if verdi.startswith(u + "/") or verdi == u:
                return pre + verdi[len(u):]
    if verdi.startswith("/") and not verdi.startswith("//"):
        return pre + verdi
    return verdi


def skal_skrive_om(content_type: str) -> bool:
    ct = (content_type or "").lower()
    return ("text/html" in ct or "text/css" in ct
            or "application/xhtml" in ct)
