#!/usr/bin/env python3
"""
SunSpec — kjenn att ein omformar/målar og lag kanalane automatisk
=================================================================
SunSpec er ein open standard for Modbus-registerkart. Ei eining som
foelgjer han legg signaturen "SunS" paa 40000, deretter ei LENKJA LISTE av
modellar: [modell-id][lengd][data...][modell-id][lengd][data...] ... og
0xFFFF til slutt.

Det tyder at vi slepp produsent-dokumentasjon. Vi les kjeda, kjenner att
modellane vi forstaar, og genererer kanalane sjoelv - med rette
skaleringsfaktorar, einingar og datatypar. Same koden virkar paa kva som
helst SunSpec-eining, ikkje berre den eine vi fann.

Funne i felt: Ginlong (Solis) "Solar inverter", modell 103 (3-fase
omformar) + 120/121/122/123/126/160. Verdiar som 65535 (uint) og -32768
(int) tyder "ikkje implementert" i standarden og skal IKKJE bli kanalar -
utan den filtreringa hadde vi laga kanalar som viser 6553,5 A.
"""

import logging

log = logging.getLogger("sunspec")

BASE = 40000                 # standard startadresse for "SunS"-signaturen
SIGNATUR = (21365, 28243)    # 0x5375 0x6E53 = "SunS"

MODELLNAMN = {
    1: "Common", 11: "Ethernet", 12: "IPv4",
    101: "Inverter, 1-fase", 102: "Inverter, delt fase",
    103: "Inverter, 3-fase",
    111: "Inverter 1-fase (float)", 112: "Inverter delt fase (float)",
    113: "Inverter 3-fase (float)",
    120: "Nameplate", 121: "Basic settings", 122: "Measurements/status",
    123: "Immediate controls", 124: "Storage", 126: "Static volt-var",
    127: "Freq-watt param", 128: "Dynamic reactive current",
    131: "Watt-PF", 132: "Volt-watt", 160: "Multiple MPPT",
    201: "Meter 1-fase", 202: "Meter delt fase", 203: "Meter 3-fase (wye)",
    204: "Meter 3-fase (delta)",
    211: "Meter 1-fase (float)", 213: "Meter 3-fase (float)",
    802: "Battery", 803: "Lithium-ion bank",
}

# Modell 101/102/103 deler oppsett. (offset, namn, eining, sf-offset,
# datatype, low, high)
INVERTER_PUNKT = [
    (0,  "AC straum",        "A",   4,  "uint16",    0, 200),
    (1,  "AC straum L1",     "A",   4,  "uint16",    0, 200),
    (2,  "AC straum L2",     "A",   4,  "uint16",    0, 200),
    (3,  "AC straum L3",     "A",   4,  "uint16",    0, 200),
    (5,  "Spenning L1-L2",   "V",   11, "uint16",    0, 700),
    (6,  "Spenning L2-L3",   "V",   11, "uint16",    0, 700),
    (7,  "Spenning L3-L1",   "V",   11, "uint16",    0, 700),
    (8,  "Spenning L1-N",    "V",   11, "uint16",    0, 400),
    (9,  "Spenning L2-N",    "V",   11, "uint16",    0, 400),
    (10, "Spenning L3-N",    "V",   11, "uint16",    0, 400),
    (12, "Aktiv effekt",     "W",   13, "int16", -100000, 100000),
    (14, "Frekvens",         "Hz",  15, "uint16",   40, 70),
    (16, "Tilsynelatande",   "VA",  17, "int16", -100000, 100000),
    (18, "Reaktiv effekt",   "var", 19, "int16", -100000, 100000),
    (20, "Effektfaktor",     "",    21, "int16",    -1, 1),
    (22, "Produsert energi", "Wh",  24, "uint32",    0, 1e9),
    (25, "DC straum",        "A",   26, "uint16",    0, 200),
    (27, "DC spenning",      "V",   28, "uint16",    0, 1500),
    (29, "DC effekt",        "W",   30, "int16", -100000, 100000),
    (31, "Temp kabinett",    "C",   35, "int16",   -40, 120),
    (32, "Temp kjoeleribbe", "C",   35, "int16",   -40, 120),
]

# Modell 203 (3-fase målar). Same idé, eige oppsett.
MAALAR_PUNKT = [
    (0,  "Straum",           "A",   4,  "int16",  -1000, 1000),
    (1,  "Straum L1",        "A",   4,  "int16",  -1000, 1000),
    (2,  "Straum L2",        "A",   4,  "int16",  -1000, 1000),
    (3,  "Straum L3",        "A",   4,  "int16",  -1000, 1000),
    (6,  "Spenning L1-N",    "V",   9,  "int16",      0, 400),
    (7,  "Spenning L2-N",    "V",   9,  "int16",      0, 400),
    (8,  "Spenning L3-N",    "V",   9,  "int16",      0, 400),
    (14, "Frekvens",         "Hz",  15, "int16",     40, 70),
    (16, "Aktiv effekt",     "W",   20, "int16", -1e6, 1e6),
    (21, "Tilsynelatande",   "VA",  25, "int16", -1e6, 1e6),
    (26, "Reaktiv effekt",   "var", 30, "int16", -1e6, 1e6),
    (31, "Effektfaktor",     "",    35, "int16",     -1, 1),
]

PUNKT_FOR_MODELL = {
    101: INVERTER_PUNKT, 102: INVERTER_PUNKT, 103: INVERTER_PUNKT,
    201: MAALAR_PUNKT, 202: MAALAR_PUNKT, 203: MAALAR_PUNKT, 204: MAALAR_PUNKT,
}

# "Ikkje implementert" i SunSpec. Slike punkt skal ikkje bli kanalar.
IKKJE_IMPL = {"uint16": 0xFFFF, "int16": -32768, "uint32": 0xFFFFFFFF}


# ---------------------------------------------------------------
#  Lesing
# ---------------------------------------------------------------
def _s16(x):
    return x - 65536 if x is not None and x > 32767 else x


def _les_ord(klient, adresse, tal=1):
    """Les `tal` 16-bits ord. Returnerer liste med None for hol.

    Eit enkelt register som ikkje svarar skal ikkje kaste heile blokka:
    ekte einingar har hol i registerrommet sitt, og eit SunSpec-oppsett
    med 50 register ville da blitt forkasta fordi eitt av dei var tomt.
    Returnerer None berre om HEILE lesinga feila.
    """
    from hub_konfig import ModbusRegister
    regs = [ModbusRegister(namn="r%d" % (adresse + i), adresse=adresse + i,
                           funksjon="holding", datatype="uint16")
            for i in range(tal)]
    try:
        verdiar = klient.les_alle(regs)
    except Exception:
        return None
    ut = [None if verdiar.get(r.namn) is None else int(verdiar[r.namn])
          for r in regs]
    return None if all(x is None for x in ut) else ut


def _tekst(ord_liste):
    b = bytearray()
    for w in ord_liste or []:
        if w is None:
            b += bytes(2)
            continue
        b += bytes([(w >> 8) & 0xFF, w & 0xFF])
    return b.decode("ascii", "replace").replace("\x00", " ").strip()


def oppdag(host: str, port: int = 502, unit_id: int = 1,
           timeout_ms: int = 3000, base: int = BASE) -> dict:
    """Er dette ei SunSpec-eining? Kva har han?

    Returnerer {sunspec: bool, produsent, modell, serienr, firmware,
    modellar: [{id, namn, adresse, lengd}]}.
    """
    ut = {"sunspec": False, "host": host, "port": port, "unit_id": unit_id,
          "produsent": "", "modell": "", "serienr": "", "firmware": "",
          "modellar": [], "melding": ""}
    import modbus_klient as mk
    klient = mk.ModbusKlient(host, port, unit_id, timeout_ms)
    if not klient.koble_til():
        ut["melding"] = klient.siste_feil or f"Could not connect to {host}:{port}"
        return ut
    try:
        sig = _les_ord(klient, base, 4)
        if not sig or None in sig[:4] or (sig[0], sig[1]) != SIGNATUR:
            ut["melding"] = (f"No SunSpec signature at {base} "
                             f"(read {sig[:2] if sig else 'nothing'})")
            return ut
        ut["sunspec"] = True

        # Common Model: id og lengd ligg på base+2 / base+3
        felles_id, felles_len = sig[2], sig[3]
        d = base + 4
        if felles_id == 1:
            ut["produsent"] = _tekst(_les_ord(klient, d, 16))
            ut["modell"] = _tekst(_les_ord(klient, d + 16, 16))
            ut["firmware"] = _tekst(_les_ord(klient, d + 40, 8))
            ut["serienr"] = _tekst(_les_ord(klient, d + 48, 16))

        # Følg lenkja liste
        adr = base + 2 + 2 + int(felles_len)
        for _ in range(24):
            hode = _les_ord(klient, adr, 2)
            if not hode:
                break
            mid, lengd = hode[0], hode[1]
            if mid is None or mid == 0xFFFF or lengd is None:
                break
            ut["modellar"].append({
                "id": mid, "namn": MODELLNAMN.get(mid, "(ukjend)"),
                "adresse": adr, "lengd": lengd,
                "kan_lese": mid in PUNKT_FOR_MODELL,
            })
            adr = adr + 2 + int(lengd)
        ut["melding"] = (f"{ut['produsent']} {ut['modell']}".strip()
                         or "SunSpec device")
    finally:
        klient.lukk()
    return ut


def lag_kanalar(host: str, port: int = 502, unit_id: int = 1,
                timeout_ms: int = 3000, base: int = BASE,
                prefiks: str = "") -> dict:
    """Bygg ModbusRegister-oppsett for alt vi forstår på eininga.

    Skaleringsfaktorane er statiske i SunSpec, så vi les dei éin gong her
    og bakar dei inn som `skalering`. Punkt som er merkte «ikkje
    implementert» blir hoppa over — elles ville vi laga kanalar som viser
    65535 som 6553,5 A.
    """
    info = oppdag(host, port, unit_id, timeout_ms, base)
    if not info["sunspec"]:
        return {"suksess": False, "melding": info["melding"], "registers": [],
                "info": info}

    import modbus_klient as mk
    klient = mk.ModbusKlient(host, port, unit_id, timeout_ms)
    if not klient.koble_til():
        return {"suksess": False, "melding": klient.siste_feil or "no connection",
                "registers": [], "info": info}

    regs = []
    hoppa = []
    try:
        for m in info["modellar"]:
            punkt = PUNKT_FOR_MODELL.get(m["id"])
            if not punkt:
                continue
            start = m["adresse"] + 2          # forbi id + lengd
            rad = _les_ord(klient, start, min(int(m["lengd"]), 60))
            if not rad:
                continue
            for off, namn, eining, sf_off, dtype, lo, hi in punkt:
                if off >= len(rad) or sf_off >= len(rad):
                    continue
                raa = rad[off]
                if raa is None or rad[sf_off] is None:
                    continue                      # hol i registerrommet
                if dtype == "uint32":
                    if off + 1 >= len(rad) or rad[off + 1] is None:
                        continue
                    raa = (rad[off] << 16) | rad[off + 1]
                elif dtype == "int16":
                    raa = _s16(raa)
                if raa == IKKJE_IMPL.get(dtype):
                    hoppa.append(namn)
                    continue
                sf = _s16(rad[sf_off])
                if sf is None or sf < -10 or sf > 10:
                    hoppa.append(namn)
                    continue
                regs.append({
                    "namn": f"{prefiks}{namn}" if prefiks else namn,
                    "adresse": start + off,
                    "funksjon": "holding",
                    "datatype": dtype,
                    "skalering": 10.0 ** sf,
                    "offset": 0.0,
                    "eining": eining,
                    "range_low": float(lo),
                    "range_high": float(hi),
                })
    finally:
        klient.lukk()

    melding = (f"{info['produsent']} {info['modell']}: {len(regs)} kanalar"
               .strip())
    if hoppa:
        melding += f" ({len(hoppa)} punkt ikkje implementert på eininga)"
    return {"suksess": bool(regs), "melding": melding, "registers": regs,
            "hoppa": hoppa, "info": info}
