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
_CSS_URL = re.compile(
    rb"""(?P<pre>url\(\s*)(?P<q>["']?)(?P<v>[^)"']+)(?P=q)(?P<post>\s*\))""",
    re.I)
_IMPORT = re.compile(
    rb"""(?P<pre>@import\s+)(?P<q>["'])(?P<v>[^"']+)(?P=q)""", re.I)


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


def loys_sti(gjeldande: str, verdi: str) -> str:
    """Gjer ei adresse absolutt, sett frae instrumentet si rot.

    `gjeldande` er stien vi henta (utan leiande /), `verdi` er slik han
    staar i HTML-en. ".."-ar blir klemde mot rota - eit instrument kan
    ikkje ha noko over rota si, og normpath gjer akkurat det.
    """
    import posixpath
    base = "/" + (gjeldande or "")
    if verdi.startswith("/"):
        ny = verdi
    else:
        ny = posixpath.join(posixpath.dirname(base), verdi)
    # Skil av spoerjestreng/fragment so normpath ikkje roerer dei
    hale = ""
    for teikn in ("?", "#"):
        i = ny.find(teikn)
        if i >= 0:
            hale = ny[i:] + hale
            ny = ny[:i]
    ny = posixpath.normpath(ny)
    if not ny.startswith("/"):
        ny = "/" + ny
    return ny + hale


_HOPP_OVER = ("#", "javascript:", "mailto:", "data:", "tel:", "about:")

_ATTR = re.compile(
    rb"""(?P<pre>\s(?:src|href|action|data-src)\s*=\s*)(?P<q>["'])(?P<v>[^"']*)(?P=q)""",
    re.I)


def skriv_om_html(kropp: bytes, pre: str, vert: str = "", havn: int = 80,
                  gjeldande: str = "") -> bytes:
    """Gjer alle adresser i svaret absolutte og peikande gjennom proxyen.

    Vi loyser opp SJOELVE, i staden for aa la browseren gjere det: gjennom
    proxyen ligg sida eit hakk djupare enn paa instrumentet, so ein "../"
    ville ete opp adressa til instrumentet.
    """
    if not kropp:
        return kropp
    b = pre.encode("utf-8")
    eigne = [u.encode("utf-8") for u in _eigne_url(vert, havn)] if vert else []

    def bytt(m):
        v = m.group("v")
        if not v:
            return m.group(0)
        for u in eigne:                     # http://<vert>/x -> /x
            if v.startswith(u):
                v = v[len(u):] or b"/"
                break
        else:
            lav = v.lower()
            if (lav.startswith((b"http://", b"https://", b"//"))
                    or any(lav.startswith(x.encode()) for x in _HOPP_OVER)):
                return m.group(0)           # peikar ut av instrumentet
        try:
            ny = loys_sti(gjeldande, v.decode("utf-8", "replace"))
        except Exception:
            return m.group(0)
        return m.group("pre") + m.group("q") + b + ny.encode("utf-8") + m.group("q")

    kropp = _ATTR.sub(bytt, kropp)
    def bytt_css(m):
        v = m.group("v").strip()
        lav = v.lower()
        if (not v or lav.startswith((b"http://", b"https://", b"//", b"data:"))
                or lav.startswith(b"#")):
            return m.group(0)
        try:
            ny_sti = loys_sti(gjeldande, v.decode("utf-8", "replace"))
        except Exception:
            return m.group(0)
        bit = b + ny_sti.encode("utf-8")
        if m.re is _IMPORT:
            return m.group("pre") + m.group("q") + bit + m.group("q")
        return (m.group("pre") + m.group("q") + bit + m.group("q")
                + m.group("post"))

    kropp = _CSS_URL.sub(bytt_css, kropp)
    kropp = _IMPORT.sub(bytt_css, kropp)
    for u in eigne:                          # rest i skript o.l.
        kropp = kropp.replace(u, b)
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
