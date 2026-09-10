# -*- coding: utf-8 -*-
"""PQube 3 — attkjenning og eitt-klikks integrering.

PQube 3 (Powerside) er ein power-kvalitet-analysator vi ofte set saman med
nodane. Han snakkar Modbus TCP (port 502) med eit fast register-kart, so vi
kan kjenne han att på nettet og leggje han inn som ein modbus-node utan at
brukaren treng slå opp adresser.

Register-offsets frå "PQube 3 Modbus Reference Manual V1.11", Classic
Register Bank, base 7000. Verdiane er ferdig-skalerte float32 (big-endian).
Same kart som frontend-presetet (`modbusPresets.ts`) — her server-side so
auto-oppdaging kan byggje noden sjølv.
"""
from __future__ import annotations

import logging

log = logging.getLogger("pqube")

BASE = 7000
PORT = 502

# (namn, offset frå base, eining, range_low, range_high)
PUNKT = [
    ("V_L1_N", 8, "V", 0, 500),
    ("V_L2_N", 10, "V", 0, 500),
    ("V_L3_N", 12, "V", 0, 500),
    ("V_L1_L2", 14, "V", 0, 900),
    ("V_L2_L3", 16, "V", 0, 900),
    ("V_L3_L1", 18, "V", 0, 900),
    ("Frekvens", 26, "Hz", 45, 65),
    ("I_L1", 28, "A", 0, 1000),
    ("I_L2", 30, "A", 0, 1000),
    ("I_L3", 32, "A", 0, 1000),
    ("I_N", 34, "A", 0, 1000),
    ("P_total", 36, "W", -1_000_000, 1_000_000),
    ("S_total", 38, "VA", 0, 1_000_000),
]


def _reg(namn, adresse, eining, lo, hi) -> dict:
    return {
        "namn": namn, "adresse": adresse, "funksjon": "holding",
        "datatype": "float32", "byte_order": "AB_CD",
        "skalering": 1.0, "offset": 0.0, "eining": eining,
        "range_low": float(lo), "range_high": float(hi),
    }


def registers(base: int = BASE) -> list:
    """Heile register-kartet som absolutte adresser (base + offset)."""
    return [_reg(n, base + off, e, lo, hi) for (n, off, e, lo, hi) in PUNKT]


def oppdag(host: str, port: int = PORT, unit_id: int = 1,
           timeout_ms: int = 2000, base: int = BASE) -> dict:
    """Ser dette ut som ein PQube 3? Les eit par nøkkelregister og sjekk.

    Ingen offisiell "identitet"-streng her — vi stadfestar ved at
    spenning og frekvens ligg i eit fysisk fornuftig område. Det skil ein
    PQube (eller ein liknande målar med same kart) frå ei vilkårleg
    Modbus-eining, utan å love meir enn vi veit.
    """
    ut = {"pqube": False, "host": host, "port": port, "unit_id": unit_id,
          "verdiar": {}, "melding": ""}
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
        les = {}
        for namn, off in (("V_L1_N", 8), ("V_L2_N", 10), ("V_L3_N", 12),
                          ("Frekvens", 26), ("P_total", 36)):
            r = ModbusRegister(namn=namn, adresse=base + off, funksjon="holding",
                               datatype="float32", byte_order="AB_CD")
            try:
                les[namn] = klient.les_register(r)
            except Exception:
                les[namn] = None
        ut["verdiar"] = {k: v for k, v in les.items() if v is not None}

        v = les.get("V_L1_N")
        f = les.get("Frekvens")
        # Fornuftig 3-fase/1-fase spenning og nettfrekvens?
        v_ok = v is not None and 40 <= v <= 1000
        f_ok = f is not None and 40 <= f <= 70
        if v_ok and f_ok:
            ut["pqube"] = True
            ut["melding"] = (f"Power meter on Modbus — {v:.0f} V, {f:.2f} Hz "
                             f"(PQube 3 register map)")
        else:
            got = ", ".join(f"{k}={v}" for k, v in ut["verdiar"].items()) or "nothing"
            ut["melding"] = (f"Modbus responded but values are not plausible "
                             f"PQube readings (read {got})")
    finally:
        klient.lukk()
    return ut


def lag_node(host: str, namn: str = "", port: int = PORT, unit_id: int = 1,
             poll_hz: float = 1.0, base: int = BASE) -> dict:
    """Bygg ein modbus_tcp-node (hub_konfig-form) for ein PQube 3."""
    return {
        "namn": namn or f"PQube 3 {host}",
        "adresse": host,
        "port": int(port),
        "aktivert": True,
        "type": "modbus_tcp",
        "modbus_unit_id": int(unit_id),
        "modbus_poll_hz": float(poll_hz),
        "modbus_timeout_ms": 2000,
        "modbus_base_adresse": 0,   # adressene er alt absolutte
        "modbus_registers": registers(base),
    }
