#!/usr/bin/env python3
"""
Modbus TCP-klient for hub-nodar
================================
Wrapper rundt pymodbus for å lese register frå eksterne Modbus-serverar
(t.d. PowerSide PQube 3 på 192.168.1.81:502).

Kvar register vert ein eigen kanal i hubben. ModbusKlient-instans har
ein persistent TCP-tilkobling og metodar for å lese ulike datatypar med
valbar byte-order.

Bruk:
    klient = ModbusKlient("192.168.1.81", 502, unit_id=1, timeout_ms=2000)
    klient.koble_til()
    verdi = klient.les_register(register)
    klient.lukk()
"""

import logging
import struct
import threading
import time
from typing import Optional, List

from hub_konfig import ModbusRegister

log = logging.getLogger('modbus_klient')


class ModbusKlient:
    """TCP-klient mot ein Modbus-server. Handterer reconnect automatisk."""

    def __init__(self, host: str, port: int = 502, unit_id: int = 1,
                 timeout_ms: int = 2000):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout_s = max(0.1, timeout_ms / 1000.0)
        self._klient = None
        self._lock = threading.Lock()
        self.tilkobla = False
        self.siste_feil: Optional[str] = None

    def koble_til(self) -> bool:
        """Opprett TCP-tilkobling. Returnerer True ved suksess."""
        with self._lock:
            if self._klient is not None:
                try:
                    self._klient.close()
                except Exception:
                    pass
                self._klient = None

            try:
                # pymodbus 3.x API
                from pymodbus.client import ModbusTcpClient
            except ImportError as e:
                self.siste_feil = f"pymodbus ikkje installert: {e}"
                self.tilkobla = False
                log.error(self.siste_feil)
                return False

            try:
                self._klient = ModbusTcpClient(
                    host=self.host,
                    port=self.port,
                    timeout=self.timeout_s,
                )
                ok = self._klient.connect()
                if ok:
                    self.tilkobla = True
                    self.siste_feil = None
                    log.info(f"Modbus TCP tilkobla: {self.host}:{self.port} unit={self.unit_id}")
                    return True
                self.tilkobla = False
                self.siste_feil = f"Kunne ikkje koble til {self.host}:{self.port}"
                return False
            except Exception as e:
                self.tilkobla = False
                self.siste_feil = str(e)
                log.warning(f"Modbus-tilkobling {self.host}:{self.port} feila: {e}")
                return False

    def lukk(self):
        """Lukk tilkoblinga."""
        with self._lock:
            if self._klient is not None:
                try:
                    self._klient.close()
                except Exception:
                    pass
                self._klient = None
            self.tilkobla = False

    def _registers_for_datatype(self, datatype: str) -> int:
        """Antal 16-bits registers som krevst for ein datatype."""
        if datatype in ("int16", "uint16"):
            return 1
        if datatype in ("int32", "uint32", "float32"):
            return 2
        return 1

    def _les_med_kompat(self, metode, address, count):
        """Kall pymodbus les-metode med kompatibilitet for ulike versjonar.

        pymodbus 2.x brukar `unit=`, 3.0-3.6 brukar `slave=`, 3.7+ brukar `device_id=`.
        Cacher rett kwarg-namn per klient for å unngå gjentekne TypeError.
        """
        if not hasattr(self, '_slave_kwarg'):
            self._slave_kwarg = None   # Ukjent enno — test ved neste kall
        # Prøv cached kwarg først
        if self._slave_kwarg:
            return metode(address=address, count=count, **{self._slave_kwarg: self.unit_id})
        # Finn rett kwarg-namn
        for kw in ("device_id", "slave", "unit"):
            try:
                rr = metode(address=address, count=count, **{kw: self.unit_id})
                self._slave_kwarg = kw
                return rr
            except TypeError:
                continue
        # Fallback: utan slave-arg (vil berre fungere for unit_id 0/255)
        return metode(address=address, count=count)

    def _les_raw(self, reg: ModbusRegister) -> Optional[List[int]]:
        """Les rå register-verdiar (liste av 16-bit words, eller [bit] for coil/discrete).

        Returnerer None ved feil.
        """
        if self._klient is None or not self.tilkobla:
            return None

        # Hopp over register som ikkje er utfylt (adresse=0 er sentinel for "fyll inn frå manual")
        if reg.adresse <= 0:
            self.siste_feil = f"Register '{reg.namn}' har adresse 0 — ikkje utfylt"
            return None

        antall = self._registers_for_datatype(reg.datatype)

        try:
            if reg.funksjon == "holding":
                rr = self._les_med_kompat(
                    self._klient.read_holding_registers, reg.adresse, antall)
            elif reg.funksjon == "input":
                rr = self._les_med_kompat(
                    self._klient.read_input_registers, reg.adresse, antall)
            elif reg.funksjon == "coil":
                rr = self._les_med_kompat(
                    self._klient.read_coils, reg.adresse, 1)
            elif reg.funksjon == "discrete":
                rr = self._les_med_kompat(
                    self._klient.read_discrete_inputs, reg.adresse, 1)
            else:
                return None

            if rr is None or rr.isError():
                self.siste_feil = f"Modbus-feil på {reg.namn}@{reg.adresse}: {rr}"
                return None

            if reg.funksjon in ("coil", "discrete"):
                return [1 if rr.bits[0] else 0]
            return list(rr.registers)
        except Exception as e:
            self.siste_feil = f"Modbus-lesefeil {reg.namn}@{reg.adresse}: {e}"
            # Markér som fråkobla så polling-løkka rekoblar
            self.tilkobla = False
            return None

    def _tolk(self, reg: ModbusRegister, words: List[int]) -> Optional[float]:
        """Tolk rå register-verdiar til float basert på datatype + byte_order."""
        if not words:
            return None

        dt = reg.datatype
        bo = reg.byte_order

        try:
            if dt == "int16":
                raw = words[0]
                if raw >= 0x8000:
                    raw -= 0x10000
                return float(raw)

            if dt == "uint16":
                return float(words[0])

            # 32-bit typar: kombiner 2 words i byte_order
            if len(words) < 2:
                return None
            hi, lo = words[0], words[1]

            # byte_order definerer rekkefølgja på dei 4 bytane (AB CD):
            # AB_CD = hi_hi hi_lo lo_hi lo_lo (big-endian, standard)
            # CD_AB = lo_hi lo_lo hi_hi hi_lo (word-swap, vanleg hos SEL/Schneider)
            # BA_DC = hi_lo hi_hi lo_lo lo_hi (byte-swap i kvar word)
            # DC_BA = lo_lo lo_hi hi_lo hi_hi (full little-endian)
            if bo == "AB_CD":
                buf = struct.pack(">HH", hi, lo)
            elif bo == "CD_AB":
                buf = struct.pack(">HH", lo, hi)
            elif bo == "BA_DC":
                buf = struct.pack("<HH", hi, lo)
            elif bo == "DC_BA":
                buf = struct.pack("<HH", lo, hi)
            else:
                buf = struct.pack(">HH", hi, lo)

            if dt == "float32":
                return float(struct.unpack(">f", buf)[0])
            if dt == "int32":
                return float(struct.unpack(">i", buf)[0])
            if dt == "uint32":
                return float(struct.unpack(">I", buf)[0])
        except struct.error as e:
            self.siste_feil = f"Tolkingsfeil {reg.namn}: {e}"
            return None

        return None

    def les_register(self, reg: ModbusRegister) -> Optional[float]:
        """Les eitt register og returner skalert fysisk verdi (eller None ved feil).

        physical = raw * skalering + offset
        """
        with self._lock:
            words = self._les_raw(reg)
        if words is None:
            return None
        raa = self._tolk(reg, words)
        if raa is None:
            return None
        return raa * reg.skalering + reg.offset

    def les_register_detaljert(self, reg: ModbusRegister) -> dict:
        """Les register og returner dict med namn, raa, verdi, feilmelding.

        Brukt av /api/modbus/test for å vise alt til brukar.
        """
        with self._lock:
            words = self._les_raw(reg)
        if words is None:
            return {
                "namn": reg.namn,
                "adresse": reg.adresse,
                "raa": None,
                "verdi": None,
                "feil": self.siste_feil or "ukjent feil",
            }
        raa = self._tolk(reg, words)
        if raa is None:
            return {
                "namn": reg.namn,
                "adresse": reg.adresse,
                "raa": words,
                "verdi": None,
                "feil": self.siste_feil or "tolking feila",
            }
        fysisk = raa * reg.skalering + reg.offset
        return {
            "namn": reg.namn,
            "adresse": reg.adresse,
            "raa": words,
            "verdi": fysisk,
            "feil": None,
        }

    def les_alle(self, registers: List[ModbusRegister]) -> dict:
        """Les alle register i sekvens. Returnerer dict adresse -> verdi (None ved feil)."""
        resultat = {}
        for reg in registers:
            resultat[reg.adresse] = self.les_register(reg)
        return resultat


def test_tilkobling(host: str, port: int, unit_id: int, timeout_ms: int,
                    registers: List[ModbusRegister]) -> dict:
    """Test ei modbus-tilkobling og les register.

    Returnerer: { suksess, melding, verdiar: [ModbusTestResultat] }
    """
    klient = ModbusKlient(host, port, unit_id, timeout_ms)
    ok = klient.koble_til()
    if not ok:
        return {
            "suksess": False,
            "melding": klient.siste_feil or f"Kunne ikkje koble til {host}:{port}",
            "verdiar": [],
        }

    verdiar = [klient.les_register_detaljert(r) for r in registers]
    klient.lukk()

    feil_count = sum(1 for v in verdiar if v.get("feil"))
    if feil_count == 0:
        melding = f"Tilkobla {host}:{port}, las {len(verdiar)} register"
    else:
        melding = f"Tilkobla {host}:{port}, men {feil_count}/{len(verdiar)} register feila"

    return {
        "suksess": True,
        "melding": melding,
        "verdiar": verdiar,
    }
