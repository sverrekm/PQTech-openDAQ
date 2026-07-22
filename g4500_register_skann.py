#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
G4500 / Elspec G4k — Modbus register-oppdagar
=============================================
Finn registerkartet EMPIRISK når du ikkje har Elspec sitt
«SMX-0708-0101 — How to Read and Write MODBUS Parameters for G4K BLACKBOX».

Kva den gjer
------------
1. `--anker`  Les kjende ankerregister og finn ut KVA KOMBINASJON som virkar:
              funksjonskode (holding/input) x word-order (AB_CD/CD_AB) x
              adressebase (0- eller 1-basert). Frekvens-registeret (~50 Hz)
              er fasiten — er den rett, er transporten din verifisert.
2. `--skann`  Skannar eit adresseområde, dekodar float32, hoppar over
              ubrukte register (Elspec melder dei som NaN = 0xFFFFFFFF) og
              flaggar verdiar som ser fysisk fornuftige ut.
3. `--json`   Skriv funna som ModbusRegister-oppføringar klare for hub-konfig.

Kjende anker (frå AggSoft sitt Elspec G4400-registerkart, same G4k-serie):
    537   Power supply temperature      (float32)
    543   Compliance name, t.d. EN 50160 (streng)
    999   Frekvens, kalkulert kvar 200 ms (float32)   <-- beste fasit
    1025  Line active power L1 (W)      (float32)
    1037  Total active power (W)        (float32)

Døme
----
    # 1) Verifiser transport + finn rett dekoding
    python3 g4500_register_skann.py --host 192.168.1.49 --port 502 --slave 1 --anker

    # 2) Skann eit område med den kombinasjonen som virka
    python3 g4500_register_skann.py --host 192.168.1.49 --skann 500 1200 \
        --funksjon holding --word-order CD_AB --json g4500_reg.json

Køyr gjerne inne i node-containeren — pymodbus er alt installert der.
"""

import sys
import json
import math
import struct
import argparse

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    sys.exit("Manglar pymodbus.  pip install 'pymodbus>=3.6.0'")

NAN32 = 0xFFFFFFFF
ANKER = {
    999: ("Frekvens", "Hz", 45.0, 65.0),
    537: ("Straumforsyning-temperatur", "degC", -20.0, 100.0),
    1025: ("Aktiv effekt L1", "W", -1e9, 1e9),
    1037: ("Total aktiv effekt", "W", -1e9, 1e9),
}


# ---------------------------------------------------------------
#  Modbus-lesing (versjons-kompatibel, same mønster som modbus_klient.py)
# ---------------------------------------------------------------
class Lesar:
    def __init__(self, host, port, slave, timeout=4.0):
        self.slave = slave
        self._kw = None
        self.k = ModbusTcpClient(host, port=port, timeout=timeout)
        if not self.k.connect():
            sys.exit(f"Fekk ikkje kontakt med {host}:{port} "
                     f"(sjekk at Modbus TCP er slått PÅ i GUI-et og kva port den brukar)")

    def _kall(self, metode, adresse, antal):
        """pymodbus 2.x=unit, 3.0-3.6=slave, 3.7+=device_id."""
        if self._kw:
            return metode(address=adresse, count=antal, **{self._kw: self.slave})
        for kw in ("device_id", "slave", "unit"):
            try:
                rr = metode(address=adresse, count=antal, **{kw: self.slave})
                self._kw = kw
                return rr
            except TypeError:
                continue
        return metode(address=adresse, count=antal)

    def les(self, adresse, antal, funksjon="holding"):
        """Returner liste med 16-bit words, eller None ved feil."""
        m = (self.k.read_holding_registers if funksjon == "holding"
             else self.k.read_input_registers)
        try:
            rr = self._kall(m, adresse, antal)
        except Exception:
            return None
        if rr is None or (hasattr(rr, "isError") and rr.isError()):
            return None
        return list(getattr(rr, "registers", []) or [])

    def lukk(self):
        try:
            self.k.close()
        except Exception:
            pass


# ---------------------------------------------------------------
#  Dekoding
# ---------------------------------------------------------------
def til_float(w0, w1, word_order="AB_CD"):
    """To 16-bit words -> float32. AB_CD = big-endian, CD_AB = word-swap."""
    hi, lo = (w0, w1) if word_order == "AB_CD" else (w1, w0)
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]


def er_ubrukt(w0, w1):
    """Elspec melder ubrukte register som NaN = 0xFFFFFFFF."""
    return ((w0 << 16) | w1) == NAN32


def til_streng(words):
    """Register-par -> ASCII (for t.d. Compliance name)."""
    b = b"".join(struct.pack(">H", w) for w in words)
    return b.split(b"\x00")[0].decode("ascii", "replace").strip()


def klassifiser(v):
    """Grov fysisk plausibilitet -> (kategori, eining) eller None."""
    a = abs(v)
    if not math.isfinite(v) or a > 1e9:
        return None
    if 45.0 <= v <= 65.0:
        return ("frekvens?", "Hz")
    if 195.0 <= v <= 260.0:
        return ("fase-spenning?", "V")
    if 340.0 <= v <= 440.0:
        return ("hovudspenning?", "V")
    if 0.01 <= v <= 100.0:
        return ("prosent/straum?", "% eller A")
    if 100.0 < a <= 1e9:
        return ("effekt/energi?", "W/var/VA")
    if v == 0.0:
        return None                      # for mange nullar til å vere nyttig
    return ("ukjend", "")


# ---------------------------------------------------------------
#  Modus 1: anker — finn rett kombinasjon
# ---------------------------------------------------------------
def køyr_anker(les: Lesar, basar=(0, 1)):
    print("Testar kombinasjonar (funksjonskode x word-order x adressebase)...")
    print("Fasit: frekvens-registeret skal gi ca. 50 Hz (eller 60).\n")
    treff = []
    for funksjon in ("holding", "input"):
        for base in basar:
            for wo in ("AB_CD", "CD_AB"):
                ok, linjer = 0, []
                for off, (namn, eining, lo, hi) in ANKER.items():
                    w = les.les(off - base, 2, funksjon)
                    if not w or len(w) < 2:
                        continue
                    if er_ubrukt(w[0], w[1]):
                        linjer.append(f"      {off:5d} {namn:28s} = NaN (ubrukt)")
                        continue
                    v = til_float(w[0], w[1], wo)
                    plausibel = math.isfinite(v) and lo <= v <= hi
                    if plausibel:
                        ok += 1
                    linjer.append(f"      {off:5d} {namn:28s} = {v:>14.4f} {eining}"
                                  f"{'  <-- plausibel' if plausibel else ''}")
                if linjer:
                    print(f"  [{funksjon:7s} base={base} {wo}]  plausible: {ok}/{len(ANKER)}")
                    for l in linjer:
                        print(l)
                    print()
                if ok:
                    treff.append((ok, funksjon, base, wo))
    if not treff:
        print("INGEN kombinasjon gav plausible verdiar.")
        print("  - Er Modbus TCP slått på i GUI-et? Kva Modbus Port og Slave Address?")
        print("  - Prøv ein annan slave-id (--slave), eller eit anna adresseområde.")
        return None
    treff.sort(reverse=True)
    ok, funksjon, base, wo = treff[0]
    print("=" * 62)
    print(f"BESTE: --funksjon {funksjon} --base {base} --word-order {wo}"
          f"   ({ok}/{len(ANKER)} anker plausible)")
    print("=" * 62)
    # Bonus: les compliance-namnet (streng) — stadfestar EN 50160-støtte
    w = les.les(543 - base, 10, funksjon)
    if w:
        s = til_streng(w)
        if s and s.isprintable():
            print(f"Compliance name (reg 543): {s!r}")
    return funksjon, base, wo


# ---------------------------------------------------------------
#  Modus 2: skann
# ---------------------------------------------------------------
def køyr_skann(les: Lesar, start, slutt, funksjon, base, wo, blokk=100):
    print(f"Skannar {start}..{slutt}  [{funksjon}, base={base}, {wo}]\n")
    words = {}
    a = start
    while a <= slutt:
        n = min(blokk, slutt - a + 2)
        w = les.les(a - base, n, funksjon)
        if w:
            for i, v in enumerate(w):
                words[a + i] = v
        else:
            # Blokka feila — prøv registervis så eitt dårleg register
            # ikkje skjuler heile blokka
            for adr in range(a, min(a + n, slutt + 1)):
                w1 = les.les(adr - base, 1, funksjon)
                if w1:
                    words[adr] = w1[0]
        a += n

    funn = []
    for adr in range(start, slutt):
        w0, w1 = words.get(adr), words.get(adr + 1)
        if w0 is None or w1 is None:
            continue
        if er_ubrukt(w0, w1):
            continue
        v = til_float(w0, w1, wo)
        kat = klassifiser(v)
        if kat:
            funn.append({"adresse": adr, "verdi": v,
                         "kategori": kat[0], "eining": kat[1]})

    print(f"Las {len(words)} register, fann {len(funn)} med plausible float32-verdiar:\n")
    print(f"  {'ADR':>6}  {'VERDI':>16}  KATEGORI")
    print(f"  {'-'*6}  {'-'*16}  {'-'*24}")
    for f in funn:
        print(f"  {f['adresse']:6d}  {f['verdi']:16.4f}  {f['kategori']} ({f['eining']})")
    print("\nMerk: float32 tek 2 register, så nabo-treff kan vere same verdi "
          "forskyvd. Kryssjekk mot web-GUI-et sine tal for å stadfeste.")
    return funn


def skriv_json(funn, sti, funksjon, wo):
    """Skriv ModbusRegister-oppføringar klare for hub-konfig."""
    ut = [{
        "namn": f"G4500 reg{f['adresse']}",
        "adresse": f["adresse"],
        "funksjon": funksjon,
        "datatype": "float32",
        "byte_order": wo,
        "skalering": 1.0,
        "offset": 0.0,
        "eining": f["eining"].split(" ")[0] if f["eining"] else "",
        "forward_berre": True,
        "_gjett": f["kategori"],
        "_lest_verdi": round(f["verdi"], 4),
    } for f in funn]
    with open(sti, "w", encoding="utf-8") as fh:
        json.dump(ut, fh, indent=2, ensure_ascii=False)
    print(f"\nSkrive {len(ut)} register til {sti}")
    print("Gi dei fornuftige namn/einingar etter kryssjekk mot GUI-et, "
          "så kan dei limast rett inn i node-konfigen.")


def main():
    p = argparse.ArgumentParser(description="Elspec G4k/G4500 Modbus register-oppdagar")
    p.add_argument("--host", required=True, help="IP til G4500 (LAN-porten der Modbus er på)")
    p.add_argument("--port", type=int, default=502, help="Modbus Port frå GUI-et (standard 502)")
    p.add_argument("--slave", type=int, default=1, help="Modbus Slave Address frå GUI-et")
    p.add_argument("--anker", action="store_true", help="Finn rett funksjon/base/word-order")
    p.add_argument("--skann", nargs=2, type=int, metavar=("START", "SLUTT"))
    p.add_argument("--funksjon", choices=("holding", "input"), default="holding")
    p.add_argument("--base", type=int, default=0, help="Adressebase: 0 eller 1")
    p.add_argument("--word-order", dest="wo", choices=("AB_CD", "CD_AB"), default="AB_CD")
    p.add_argument("--json", help="Skriv funna til denne fila")
    a = p.parse_args()

    if not a.anker and not a.skann:
        p.error("Vel --anker (start her) eller --skann START SLUTT")

    les = Lesar(a.host, a.port, a.slave)
    print(f"Tilkopla {a.host}:{a.port} (slave {a.slave})\n")
    try:
        funksjon, base, wo = a.funksjon, a.base, a.wo
        if a.anker:
            res = køyr_anker(les)
            if res:
                funksjon, base, wo = res
            if not a.skann:
                return
            print()
        funn = køyr_skann(les, a.skann[0], a.skann[1], funksjon, base, wo)
        if a.json and funn:
            skriv_json(funn, a.json, funksjon, wo)
    finally:
        les.lukk()


if __name__ == "__main__":
    main()
