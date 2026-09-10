# -*- coding: utf-8 -*-
"""Elspec G4500 BlackBox — Modbus-registerkart og integrering.

G4500 er ein power-kvalitet-analysator (Elspec). Han snakkar Modbus RTU over
RS-485 (unit 159 hos oss), so for å lese han over nett må RS-485 gå via ein
RS-485→Modbus-TCP-gateway. Register-adressene er 32-bit IEEE-754 float
(to påfølgjande 16-bit register). Funksjonskode er FC04 (input) ELLER FC03
(holding) avhengig av modell — vi prøver begge og brukar den som svarar.

Kart frå Elspec (desimal-adresser):
  999  Frekvens (Hz)
  1123/1125/1127  Spenning RMS L1/L2/L3 (V)
  1131/1133/1135  Straum RMS L1/L2/L3 (A)
  1025/1027/1029  Aktiv effekt P L1/L2/L3 (W)
  1037            Total aktiv effekt (W)
  1041/1043/1045  Reaktiv effekt Q L1/L2/L3 (VAr)
  1053            Total reaktiv effekt (VAr)
  1159            Intern temperatur (°C)
"""
from __future__ import annotations

import logging

log = logging.getLogger("g4500")

PORT = 502
UNIT = 159          # RTU-slaveadressa Sverre sette på BlackBox-en

# (namn, adresse, eining, range_low, range_high)
PUNKT = [
    ("Frekvens", 999, "Hz", 40, 70),
    ("V_L1", 1123, "V", 0, 500),
    ("V_L2", 1125, "V", 0, 500),
    ("V_L3", 1127, "V", 0, 500),
    ("I_L1", 1131, "A", 0, 6000),
    ("I_L2", 1133, "A", 0, 6000),
    ("I_L3", 1135, "A", 0, 6000),
    ("P_L1", 1025, "W", -1_000_000, 1_000_000),
    ("P_L2", 1027, "W", -1_000_000, 1_000_000),
    ("P_L3", 1029, "W", -1_000_000, 1_000_000),
    ("P_total", 1037, "W", -3_000_000, 3_000_000),
    ("Q_L1", 1041, "VAr", -1_000_000, 1_000_000),
    ("Q_L2", 1043, "VAr", -1_000_000, 1_000_000),
    ("Q_L3", 1045, "VAr", -1_000_000, 1_000_000),
    ("Q_total", 1053, "VAr", -3_000_000, 3_000_000),
    ("Temp_intern", 1159, "°C", -20, 120),
]


def _reg(namn, adresse, eining, lo, hi, funksjon="holding") -> dict:
    return {
        "namn": namn, "adresse": adresse, "funksjon": funksjon,
        "datatype": "float32", "byte_order": "AB_CD",  # Elspec: big-endian
        "skalering": 1.0, "offset": 0.0, "eining": eining,
        "range_low": float(lo), "range_high": float(hi),
    }


def registers(funksjon: str = "holding") -> list:
    return [_reg(n, a, e, lo, hi, funksjon) for (n, a, e, lo, hi) in PUNKT]


def oppdag(host: str, port: int = PORT, unit_id: int = UNIT,
           timeout_ms: int = 2000) -> dict:
    """Svarar det ein G4500 her? Prøv FC03 og FC04, sjekk fornuftige verdiar.

    Returnerer m.a. `funksjon` = den funksjonskoden som svarte, so
    node-oppsettet brukar rett kode.
    """
    ut = {"g4500": False, "host": host, "port": port, "unit_id": unit_id,
          "funksjon": "", "verdiar": {}, "melding": ""}
    try:
        import modbus_klient as mk
        from hub_konfig import ModbusRegister
    except Exception as e:
        ut["melding"] = f"modbus unavailable: {e}"
        return ut

    klient = mk.ModbusKlient(host, port, unit_id, timeout_ms)
    if not klient.koble_til():
        ut["melding"] = klient.siste_feil or f"Could not connect to {host}:{port}"
        return ut
    try:
        for funksjon in ("holding", "input"):
            les = {}
            for namn, adr in (("Frekvens", 999), ("V_L1", 1123), ("V_L2", 1125)):
                r = ModbusRegister(namn=namn, adresse=adr, funksjon=funksjon,
                                   datatype="float32", byte_order="AB_CD")
                try:
                    les[namn] = klient.les_register(r)
                except Exception:
                    les[namn] = None
            f = les.get("Frekvens")
            v = les.get("V_L1")
            f_ok = f is not None and 40 <= f <= 70
            v_ok = v is not None and 0 <= v <= 1000
            if f_ok and v_ok:
                ut.update(g4500=True, funksjon=funksjon,
                          verdiar={k: x for k, x in les.items() if x is not None},
                          melding=("Elspec G4500 — %.0f V, %.2f Hz (FC%s)"
                                   % (v, f, "03" if funksjon == "holding" else "04")))
                return ut
        ut["melding"] = ("Modbus responded but no plausible G4500 readings "
                         "(check unit id / RS-485 gateway / byte order)")
    finally:
        klient.lukk()
    return ut


def lag_node(host: str, namn: str = "", port: int = PORT, unit_id: int = UNIT,
             poll_hz: float = 1.0, funksjon: str = "holding") -> dict:
    """Bygg ein modbus_tcp-node (hub_konfig-form) for ein G4500."""
    return {
        "namn": namn or f"Elspec G4500 {host}",
        "adresse": host,
        "port": int(port),
        "aktivert": True,
        "type": "modbus_tcp",
        "modbus_unit_id": int(unit_id),
        "modbus_poll_hz": float(poll_hz),
        "modbus_timeout_ms": 2000,
        "modbus_base_adresse": 0,
        "modbus_registers": registers(funksjon),
    }
