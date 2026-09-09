#!/usr/bin/env python3
"""
MCP-server for OpenDackoConteiner (SIRIUS DAQ-bro).

Gir Claude direkte tilgang til systemstatus, logger, kanalverdiar
og kontrolloperasjonar via Model Context Protocol (stdio-transport).

Bruk:
  python opendacko_mcp.py

Konfigurasjon:
  OPENDACKO_URL  -  Base-URL til web_ui.py (default: http://192.168.1.160:8080)
"""

import os
import json

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("OPENDACKO_URL", "http://192.168.1.160:8080").rstrip("/")

mcp = FastMCP(
    "opendacko",
    instructions="SIRIUS DAQ bridge — status, logger, kanalar og kontroll",
)

# ---------------------------------------------------------------------------
# Hjelpefunksjonar
# ---------------------------------------------------------------------------

async def _get(path: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    """HTTP GET med feilhandtering."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout) as client:
        r = await client.get(path, params=params)
        r.raise_for_status()
        return r.json()


async def _post(path: str, payload: dict | None = None, timeout: float = 60.0) -> dict:
    """HTTP POST med lengre timeout (kontrolloperasjonar tek tid)."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout) as client:
        r = await client.post(path, json=payload)
        r.raise_for_status()
        return r.json()


def _fmt(data: dict, title: str = "") -> str:
    """Formater dict som lesbar strukturert tekst."""
    lines = []
    if title:
        lines.append(f"=== {title} ===")
    for key, val in data.items():
        if isinstance(val, dict):
            lines.append(f"\n-- {key} --")
            for k2, v2 in val.items():
                lines.append(f"  {k2}: {v2}")
        elif isinstance(val, list):
            lines.append(f"{key}:")
            if not val:
                lines.append("  (tom)")
            for item in val:
                if isinstance(item, dict):
                    parts = ", ".join(f"{k}={v}" for k, v in item.items())
                    lines.append(f"  - {parts}")
                else:
                    lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {val}")
    return "\n".join(lines)


def _err(e: Exception) -> str:
    """Einheitleg feilmelding."""
    if isinstance(e, httpx.ConnectError):
        return f"Tilkoblingsfeil: Kan ikkje naa {BASE_URL} — er Pi-en paa?"
    if isinstance(e, httpx.TimeoutException):
        return f"Timeout: {BASE_URL} svarte ikkje i tide"
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    return f"Feil: {e}"


# ---------------------------------------------------------------------------
# Status-verktoy (les-bare)
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_system_status() -> str:
    """Overall system health: IP, channels, servers, USB devices."""
    try:
        data = await _get("/api/status")
        return _fmt(data, "System Status")
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_sirius_status() -> str:
    """SIRIUS driver status: connected, streaming, EP2, data rate."""
    try:
        data = await _get("/api/sirius/status")
        return _fmt(data, "SIRIUS Driver Status")
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_opendaq_status() -> str:
    """openDAQ bridge status: OPC-UA/streaming port status, active state."""
    try:
        data = await _get("/api/opendaq/status")
        return _fmt(data, "openDAQ Bridge Status")
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_logs(count: int = 100) -> str:
    """Recent log lines from the ring buffer.

    Args:
        count: Number of log lines to fetch (max 500, default 100).
    """
    try:
        data = await _get("/api/logg", params={"antall": min(count, 500)})
        lines = data.get("linjer", [])
        header = f"=== Logg ({len(lines)} linjer) ==="
        return header + "\n" + "\n".join(lines)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_channel_values() -> str:
    """Channel values from openDAQ bridge with source indicator (D/S/~)."""
    try:
        data = await _get("/api/opendaq/verdiar")
        if not data or "feil" in data:
            return _fmt(data, "Kanalverdiar")
        lines = ["=== Kanalverdiar (openDAQ) ==="]
        for ch, info in data.items():
            if isinstance(info, dict):
                val = info.get("verdi", info.get("value", "?"))
                src = info.get("kjelde", info.get("source", ""))
                lines.append(f"  {ch}: {val}  [{src}]")
            else:
                lines.append(f"  {ch}: {info}")
        return "\n".join(lines)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_device_info() -> str:
    """SIRIUS device identification: serial number, firmware, slot info."""
    try:
        data = await _get("/api/sirius/info")
        return _fmt(data, "SIRIUS Device Info")
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_channel_data() -> str:
    """Raw ADC data snapshot from the driver (last samples per channel)."""
    try:
        data = await _get("/api/sirius/data")
        if not data:
            return "Ingen data (streaming ikkje aktiv?)"
        lines = ["=== ADC Data Snapshot ==="]
        for ch, info in data.items():
            if isinstance(info, dict):
                siste = info.get("siste", "?")
                antall = info.get("antall", 0)
                lines.append(f"  {ch}: siste={siste}, samples={antall}")
            else:
                lines.append(f"  {ch}: {info}")
        return "\n".join(lines)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_channel_config() -> str:
    """Channel configuration: names, types, ranges, units."""
    try:
        data = await _get("/api/kanalar")
        if not data:
            return "Ingen kanalkonfigurasjon"
        lines = ["=== Kanalkonfigurasjon ==="]
        for ch in data:
            if isinstance(ch, dict):
                namn = ch.get("namn", ch.get("name", "?"))
                typ = ch.get("type", ch.get("typ", "?"))
                omfang = ch.get("omfang", ch.get("range", "?"))
                eining = ch.get("eining", ch.get("unit", ""))
                lines.append(f"  {namn}: type={typ}, range={omfang}, unit={eining}")
            else:
                lines.append(f"  {ch}")
        return "\n".join(lines)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_live_comparison() -> str:
    """Live channel values: openDAQ vs driver side by side."""
    try:
        data = await _get("/api/kanalar/live")
        lines = ["=== Live Comparison (openDAQ vs Driver) ==="]
        odaq = data.get("opendaq", {})
        drv = data.get("driver", {})
        all_ch = sorted(set(list(odaq.keys()) + list(drv.keys())))
        if not all_ch:
            return "Ingen live-data tilgjengeleg"
        lines.append(f"{'Kanal':<20} {'openDAQ':>12} {'Driver':>12}")
        lines.append("-" * 46)
        for ch in all_ch:
            o_val = odaq.get(ch, {})
            d_val = drv.get(ch, {})
            o_str = str(o_val.get("verdi", o_val) if isinstance(o_val, dict) else o_val)
            d_str = str(d_val.get("siste", d_val) if isinstance(d_val, dict) else d_val)
            lines.append(f"  {ch:<18} {o_str:>12} {d_str:>12}")
        return "\n".join(lines)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_usbip_status() -> str:
    """USB/IP sharing status."""
    try:
        data = await _get("/api/usbip/status")
        return _fmt(data, "USB/IP Status")
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Kontroll-verktoy (skriveoperasjonar)
# ---------------------------------------------------------------------------

@mcp.tool()
async def reconnect_sirius() -> str:
    """Reconnect to SIRIUS device + automatic EP2 recovery.

    This stops current streaming, reconnects USB, and restarts streaming.
    """
    try:
        data = await _post("/api/sirius/rekoble", timeout=60.0)
        ok = data.get("suksess", False)
        msg = data.get("melding", "")
        status = "OK" if ok else "FEILET"
        return f"Rekobling: {status}\n{msg}"
    except Exception as e:
        return _err(e)


@mcp.tool()
async def recover_ep2() -> str:
    """EP2 ADC recovery — tries multiple strategies to revive the data endpoint."""
    try:
        data = await _post("/api/sirius/gjenoppliv-ep2", timeout=60.0)
        ok = data.get("suksess", False)
        msg = data.get("melding", "")
        status = "OK" if ok else "FEILET"
        return f"EP2 Gjenoppliving: {status}\n{msg}"
    except Exception as e:
        return _err(e)


@mcp.tool()
async def restart_opendaq_bridge() -> str:
    """Restart OPC-UA + native streaming servers.

    May trigger a container restart if ports are stuck.
    """
    try:
        data = await _post("/api/opendaq/restart", timeout=60.0)
        ok = data.get("suksess", False)
        msg = data.get("melding", "")
        status = "OK" if ok else "FEILET"
        return f"openDAQ Restart: {status}\n{msg}"
    except Exception as e:
        return _err(e)


@mcp.tool()
async def start_streaming() -> str:
    """Start ADC streaming from SIRIUS."""
    try:
        data = await _post("/api/sirius/start")
        ok = data.get("suksess", False)
        msg = data.get("melding", "")
        status = "OK" if ok else "FEILET"
        return f"Start Streaming: {status}\n{msg}"
    except Exception as e:
        return _err(e)


@mcp.tool()
async def stop_streaming() -> str:
    """Stop ADC streaming from SIRIUS."""
    try:
        data = await _post("/api/sirius/stopp")
        ok = data.get("suksess", False)
        msg = data.get("melding", "")
        status = "OK" if ok else "FEILET"
        return f"Stopp Streaming: {status}\n{msg}"
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Analyse-verktoy (avansert feilsoking)
# ---------------------------------------------------------------------------

@mcp.tool()
async def run_usb_probe() -> str:
    """Run USB probe on the SIRIUS device (takes ~30s).

    Starts a background probe — use get_probe_status to check results.
    """
    try:
        data = await _post("/api/probe/kjor", timeout=10.0)
        ok = data.get("suksess", False)
        msg = data.get("melding", "")
        if ok:
            return f"Probe starta. Bruk get_probe_status for aa sjekke resultat."
        return f"Probe feilet: {msg}"
    except Exception as e:
        return _err(e)


@mcp.tool()
async def get_probe_status() -> str:
    """Get USB probe result/output."""
    try:
        data = await _get("/api/probe/status")
        status = data.get("status", "unknown")
        output = data.get("output", "")
        lines = [f"=== Probe Status: {status} ==="]
        if output:
            lines.append(output)
        return "\n".join(lines)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def send_raw_command(command: str, poll: bool = False) -> str:
    """Send a raw hex USB command to SIRIUS (for debugging).

    Args:
        command: Hex string (e.g. "AE1F0C").
        poll: If True, use AD+B1 poll mechanism.
    """
    try:
        data = await _post(
            "/api/debug/kommando",
            payload={"kommando": command, "poll": poll},
            timeout=30.0,
        )
        if "feil" in data:
            return f"Feil: {data['feil']}"
        lines = [f"=== Raw Command ==="]
        lines.append(f"Sendt:  {data.get('sendt', '?')} ({data.get('lengde_sendt', '?')} bytes)")
        lines.append(f"Modus:  {data.get('modus', '?')}")
        lines.append(f"Svar:   {data.get('svar', '(tomt)')} ({data.get('lengde_svar', 0)} bytes)")
        lines.append(f"ASCII:  {data.get('svar_ascii', '')}")
        if data.get("all_ff"):
            lines.append("  (!) Alle bytes er 0xFF — tom respons")
        if data.get("all_00"):
            lines.append("  (!) Alle bytes er 0x00 — null-respons")
        return "\n".join(lines)
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Oppstart
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
