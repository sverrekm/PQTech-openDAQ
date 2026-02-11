#!/usr/bin/env python3
"""
SIRIUS Hoeynivaa-driver (Lag 2)
=================================
Brukervenlig driver med tilkobling, initialisering, konfigurasjon og streaming.

Bygger paa sirius_protokoll_impl.py for all USB-kommunikasjon.

Bruk:
    from sirius_driver import SiriusDriver

    driver = SiriusDriver()
    driver.koble_til()
    print(driver.enhetsinfo)

    driver.start_streaming(callback=min_callback)
    time.sleep(5)
    driver.stopp_streaming()

    driver.koble_fra()
"""

import struct
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

import numpy as np

try:
    import usb.core
    import usb.util
except ImportError:
    usb = None

from sirius_protokoll_impl import (
    SiriusProtokoll,
    DEWESOFT_VID, SIRIUS_PID,
    EP_ADC_IN, EP_CTRL_IN, EP_SYNC_IN,
    ALLE_SLOTTER, SLOT_0,
    REG_SLOT_INFO, REG_ADC_KONFIG, REG_KALIBRERING,
    OP_LES, OP_SKRIV,
    SiriusFeil, SiriusUSBFeil, SiriusPollTimeout, SiriusIkkeFunnet,
    TIMEOUT_DATA,
)

log = logging.getLogger('sirius_driver')


# --- Dataklasser ---

@dataclass
class SlotInfo:
    """Informasjon om en enkelt slot (analog inngangsmodul)."""
    slot_id: int = 0
    kanal_nummer: int = 0
    produsent: str = ""
    maskinvare_del: str = ""
    firmware_versjon: str = ""
    aktiv: bool = False
    adc_konfig: bytes = field(default_factory=bytes)
    kalibrering: bytes = field(default_factory=bytes)


@dataclass
class EnhetsInfo:
    """Samlet enhetsinformasjon."""
    enhetsstreng: str = ""        # "DEWEUSB7"
    serienummer: str = ""
    enhetstype: bytes = field(default_factory=bytes)
    antall_slotter: int = 4
    slotter: list = field(default_factory=list)


@dataclass
class MaaleKonfig:
    """Konfigurasjon for en maaling."""
    sample_rate: int = 1000
    aktive_kanaler: list = field(default_factory=lambda: [0, 1, 2, 3])
    varighet_sek: float = 5.0


class SiriusDriver:
    """
    Hoeynivaa driver for Dewesoft SIRIUS.

    Haandterer tilkobling, initialisering, konfigurasjon og streaming.
    """

    def __init__(self):
        self._proto: Optional[SiriusProtokoll] = None
        self._dev = None
        self._tilkoblet = False
        self._streamer = False
        self._stopp_event = threading.Event()
        self._adc_traad: Optional[threading.Thread] = None
        self._heartbeat_traad: Optional[threading.Thread] = None
        self._data_callback: Optional[Callable] = None
        self._data_buffer = []
        self._buffer_lock = threading.Lock()
        self._buffer_storrelse = 1000
        self._enhetsinfo = EnhetsInfo()
        self._konfig = MaaleKonfig()
        self._rekoble_forsok = 0
        self._maks_rekoble = 3
        self._data_rate_bytes = 0
        self._data_rate_ts = time.time()
        self._data_rate_kbs = 0.0
        self._siste_data = {}
        self._siste_data_lock = threading.Lock()

    @property
    def enhetsinfo(self) -> EnhetsInfo:
        return self._enhetsinfo

    @property
    def konfig(self) -> MaaleKonfig:
        return self._konfig

    @property
    def streamer(self) -> bool:
        return self._streamer

    @property
    def data_rate_kbs(self) -> float:
        return self._data_rate_kbs

    @property
    def siste_data(self) -> dict:
        with self._siste_data_lock:
            return dict(self._siste_data)

    # ---- Tilkobling ----

    def koble_til(self):
        """
        Finn og koble til SIRIUS via USB.

        Raises:
            SiriusIkkeFunnet: Hvis SIRIUS ikke finnes paa USB
            SiriusUSBFeil: Ved USB-feil
        """
        if usb is None:
            raise SiriusFeil("pyusb er ikke installert (pip install pyusb)")

        log.info(f"Soeker etter SIRIUS (VID=0x{DEWESOFT_VID:04X}, PID=0x{SIRIUS_PID:04X})...")

        dev = usb.core.find(idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID)
        if dev is None:
            raise SiriusIkkeFunnet(
                f"SIRIUS ikke funnet paa USB "
                f"(VID=0x{DEWESOFT_VID:04X}, PID=0x{SIRIUS_PID:04X})"
            )

        log.info(f"SIRIUS funnet: Bus {dev.bus}, Adresse {dev.address}")

        # Frigjor fra kernel-driver (Linux)
        try:
            for cfg in dev:
                for intf in cfg:
                    if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                        log.debug(f"Frigjor kernel-driver for interface {intf.bInterfaceNumber}")
                        dev.detach_kernel_driver(intf.bInterfaceNumber)
        except (usb.core.USBError, NotImplementedError):
            pass

        # Sett konfigurasjon
        try:
            dev.set_configuration()
        except usb.core.USBError as e:
            log.warning(f"set_configuration feilet (kan allerede vaere satt): {e}")

        self._dev = dev
        self._proto = SiriusProtokoll(dev)
        self._tilkoblet = True
        self._rekoble_forsok = 0

        # Kjoer initialisering
        self._initialiser()

        log.info("SIRIUS tilkoblet og initialisert")

    def koble_fra(self):
        """Stopp streaming og frigjor USB-enhet."""
        if self._streamer:
            self.stopp_streaming()

        self._tilkoblet = False

        if self._dev is not None:
            try:
                usb.util.dispose_resources(self._dev)
            except Exception as e:
                log.debug(f"Feil ved frigjoring av USB: {e}")
            self._dev = None
            self._proto = None

        log.info("SIRIUS frakoblet")

    def er_tilkoblet(self) -> bool:
        """Sjekk om enheten er tilkoblet."""
        if not self._tilkoblet or self._dev is None:
            return False
        # Enkel helsesjekk
        try:
            self._proto.send_telemetri()
            return True
        except Exception:
            self._tilkoblet = False
            return False

    def rekoble(self) -> bool:
        """
        Proev aa koble til paa nytt.

        Returns:
            True ved vellykket rekonnektering
        """
        log.info("Proever aa rekoble til SIRIUS...")
        try:
            self.koble_fra()
            time.sleep(1)
            self.koble_til()
            return True
        except SiriusFeil as e:
            self._rekoble_forsok += 1
            log.error(f"Rekonnektering feilet ({self._rekoble_forsok}/{self._maks_rekoble}): {e}")
            return False

    # ---- Initialisering ----

    def _initialiser(self):
        """
        Kjoer full init-sekvens mot SIRIUS.

        1. Les enhetsidentifikasjon (0xE3)
        2. Les enhetstype (0xE4)
        3. For hver slot: les info, ADC-konfig, kalibrering
        4. Send telemetri (heartbeat-sjekk)
        5. Flush dataendepunkter
        """
        log.info("Initialiserer SIRIUS...")

        # 1. Enhetsidentifikasjon
        try:
            ident = self._proto.les_enhetsid()
            self._enhetsinfo.enhetsstreng = ident.get('enhetsstreng', '')
            self._enhetsinfo.serienummer = ident.get('serienummer', '')
            log.info(f"  Enhetsstreng: {self._enhetsinfo.enhetsstreng}")
            log.info(f"  Serienummer:  {self._enhetsinfo.serienummer}")
        except SiriusUSBFeil as e:
            log.warning(f"  Kunne ikke lese enhetsid: {e}")

        # 2. Enhetstype
        try:
            devtype = self._proto.les_enhetstype()
            self._enhetsinfo.enhetstype = devtype
            log.info(f"  Enhetstype:   {devtype[:4].hex() if len(devtype) >= 4 else devtype.hex()}")
        except SiriusUSBFeil as e:
            log.warning(f"  Kunne ikke lese enhetstype: {e}")

        # 3. Slot-informasjon
        self._enhetsinfo.slotter = []
        for i, slot_addr in enumerate(ALLE_SLOTTER):
            slot = SlotInfo(slot_id=slot_addr, kanal_nummer=i)
            try:
                self._les_slot_info(slot)
                slot.aktiv = True
            except (SiriusPollTimeout, SiriusUSBFeil) as e:
                log.debug(f"  Slot {i} (0x{slot_addr:02X}): ikke aktiv ({e})")
                slot.aktiv = False
            self._enhetsinfo.slotter.append(slot)

        aktive = sum(1 for s in self._enhetsinfo.slotter if s.aktiv)
        log.info(f"  Aktive slotter: {aktive}/{len(ALLE_SLOTTER)}")

        # 4. Telemetri (heartbeat-sjekk)
        try:
            tele = self._proto.send_telemetri()
            log.info(f"  Telemetri OK ({len(tele)}B)")
        except SiriusUSBFeil as e:
            log.warning(f"  Telemetri feilet: {e}")

        # 5. Flush dataendepunkter
        for ep in [EP_ADC_IN, EP_CTRL_IN, EP_SYNC_IN]:
            self._proto.flush_endepunkt(ep)

        log.info("Initialisering fullfoert")

    def _les_slot_info(self, slot: SlotInfo):
        """Les detaljert informasjon om en slot."""
        # Sub-register 0x01-0x06 under REG_SLOT_INFO
        for sub_reg in range(0x01, 0x07):
            try:
                data = self._proto.les_register(slot.slot_id, REG_SLOT_INFO)
                if sub_reg == 0x01:
                    slot.produsent = data[:16].rstrip(b'\x00').decode('ascii', errors='replace')
                elif sub_reg == 0x02:
                    slot.maskinvare_del = data[:16].rstrip(b'\x00').decode('ascii', errors='replace')
                elif sub_reg == 0x03:
                    slot.firmware_versjon = data[:8].rstrip(b'\x00').decode('ascii', errors='replace')
            except (SiriusPollTimeout, SiriusUSBFeil):
                pass

        # ADC-konfigurasjon
        try:
            slot.adc_konfig = self._proto.les_register(slot.slot_id, REG_ADC_KONFIG)
        except (SiriusPollTimeout, SiriusUSBFeil):
            pass

        # Kalibrering
        try:
            slot.kalibrering = self._proto.les_register(slot.slot_id, REG_KALIBRERING)
        except (SiriusPollTimeout, SiriusUSBFeil):
            pass

        log.info(
            f"  Slot {slot.kanal_nummer} (0x{slot.slot_id:02X}): "
            f"produsent='{slot.produsent}', "
            f"HW='{slot.maskinvare_del}', "
            f"FW='{slot.firmware_versjon}'"
        )

    # ---- Streaming ----

    def start_streaming(self, callback=None, buffer_storrelse=1000):
        """
        Start ADC-datastreaming i bakgrunnstraader.

        Args:
            callback: Funksjon som kalles med (kanal_data: dict) for hvert datablokk
            buffer_storrelse: Maks antall rammer i buffer
        """
        if self._streamer:
            log.warning("Streaming kjorer allerede")
            return

        if not self._tilkoblet:
            raise SiriusFeil("Ikke tilkoblet - kall koble_til() foerst")

        self._data_callback = callback
        self._buffer_storrelse = buffer_storrelse
        self._stopp_event.clear()
        self._streamer = True
        self._data_rate_bytes = 0
        self._data_rate_ts = time.time()

        # Start ADC-leser-traad
        self._adc_traad = threading.Thread(
            target=self._adc_leser_loop,
            name="sirius-adc",
            daemon=True,
        )
        self._adc_traad.start()

        # Start heartbeat-traad
        self._heartbeat_traad = threading.Thread(
            target=self._heartbeat_loop,
            name="sirius-heartbeat",
            daemon=True,
        )
        self._heartbeat_traad.start()

        log.info("Streaming startet")

    def stopp_streaming(self):
        """Stopp streaming og vent paa at traader avslutter."""
        if not self._streamer:
            return

        log.info("Stopper streaming...")
        self._stopp_event.set()
        self._streamer = False

        if self._adc_traad and self._adc_traad.is_alive():
            self._adc_traad.join(timeout=5)
        if self._heartbeat_traad and self._heartbeat_traad.is_alive():
            self._heartbeat_traad.join(timeout=5)

        self._adc_traad = None
        self._heartbeat_traad = None
        log.info("Streaming stoppet")

    def hent_data(self, antall_rammer=None):
        """
        Hent buffret data.

        Args:
            antall_rammer: Maks antall rammer, None=alle

        Returns:
            list: Liste med kanal-data dicts
        """
        with self._buffer_lock:
            if antall_rammer is None:
                data = list(self._data_buffer)
                self._data_buffer.clear()
            else:
                data = self._data_buffer[:antall_rammer]
                self._data_buffer = self._data_buffer[antall_rammer:]
        return data

    def _adc_leser_loop(self):
        """Bakgrunnstraad: les ADC-data fra EP2 og parser."""
        feil_teller = 0

        while not self._stopp_event.is_set():
            try:
                raa = self._proto.les_adc_data(
                    storrelse=16384,
                    timeout=TIMEOUT_DATA
                )

                if raa:
                    self._data_rate_bytes += len(raa)
                    naa = time.time()
                    dt = naa - self._data_rate_ts
                    if dt >= 1.0:
                        self._data_rate_kbs = (self._data_rate_bytes / 1024.0) / dt
                        self._data_rate_bytes = 0
                        self._data_rate_ts = naa

                    # Parser int16-data og deinterlev kanaler
                    antall_kanaler = max(
                        1,
                        sum(1 for s in self._enhetsinfo.slotter if s.aktiv)
                    )
                    kanal_data = self._deinterlev_data(raa, antall_kanaler)

                    # Oppdater siste data
                    with self._siste_data_lock:
                        self._siste_data = kanal_data

                    # Callback
                    if self._data_callback:
                        try:
                            self._data_callback(kanal_data)
                        except Exception as e:
                            log.warning(f"Callback-feil: {e}")

                    # Buffer
                    with self._buffer_lock:
                        self._data_buffer.append(kanal_data)
                        while len(self._data_buffer) > self._buffer_storrelse:
                            self._data_buffer.pop(0)

                feil_teller = 0

            except SiriusUSBFeil as e:
                if self._stopp_event.is_set():
                    break
                feil_teller += 1
                if "timeout" in str(e).lower():
                    log.debug(f"ADC timeout (hopper over)")
                else:
                    log.warning(f"ADC-feil ({feil_teller}): {e}")

                if feil_teller >= self._maks_rekoble:
                    log.error("For mange ADC-feil - proever rekobling")
                    if not self.rekoble():
                        log.error("Rekobling feilet - stopper streaming")
                        self._streamer = False
                        break
                    feil_teller = 0

            except Exception as e:
                if self._stopp_event.is_set():
                    break
                log.error(f"Uventet feil i ADC-loop: {e}")
                time.sleep(0.1)

    def _heartbeat_loop(self):
        """Bakgrunnstraad: send AE telemetri + les EP4 periodisk."""
        while not self._stopp_event.is_set():
            try:
                self._proto.send_telemetri()
            except SiriusUSBFeil as e:
                log.debug(f"Heartbeat telemetri feilet: {e}")

            try:
                self._proto.les_ctrl_data(timeout=500)
            except SiriusUSBFeil:
                pass

            # Vent 2 sekunder mellom heartbeats
            self._stopp_event.wait(timeout=2.0)

    @staticmethod
    def _deinterlev_data(raa_bytes, antall_kanaler):
        """
        Split interleaved int16-data til per-kanal arrays.

        Args:
            raa_bytes: Raa bytes fra EP2
            antall_kanaler: Antall aktive kanaler

        Returns:
            dict: {'kanal_0': np.array, 'kanal_1': np.array, ...}
        """
        if len(raa_bytes) < 2:
            return {}

        # Konverter til int16-array
        antall_samples = len(raa_bytes) // 2
        alle = np.frombuffer(raa_bytes[:antall_samples * 2], dtype=np.int16)

        # Deinterlev
        resultat = {}
        trim = (len(alle) // antall_kanaler) * antall_kanaler
        interlev = alle[:trim]

        for k in range(antall_kanaler):
            resultat[f'kanal_{k}'] = interlev[k::antall_kanaler].copy()

        return resultat

    # ---- Info-metoder ----

    def hent_status(self) -> dict:
        """Returner driver-status som dict (for API/web)."""
        return {
            "tilkoblet": self._tilkoblet,
            "streamer": self._streamer,
            "enhetsstreng": self._enhetsinfo.enhetsstreng,
            "serienummer": self._enhetsinfo.serienummer,
            "enhetstype": self._enhetsinfo.enhetstype.hex() if self._enhetsinfo.enhetstype else "",
            "antall_slotter": self._enhetsinfo.antall_slotter,
            "aktive_slotter": sum(1 for s in self._enhetsinfo.slotter if s.aktiv),
            "slotter": [
                {
                    "slot_id": f"0x{s.slot_id:02X}",
                    "kanal": s.kanal_nummer,
                    "produsent": s.produsent,
                    "maskinvare": s.maskinvare_del,
                    "firmware": s.firmware_versjon,
                    "aktiv": s.aktiv,
                }
                for s in self._enhetsinfo.slotter
            ],
            "sample_rate": self._konfig.sample_rate,
            "data_rate_kbs": round(self._data_rate_kbs, 1),
        }

    def __repr__(self):
        return (
            f"SiriusDriver("
            f"tilkoblet={self._tilkoblet}, "
            f"streamer={self._streamer}, "
            f"enhet='{self._enhetsinfo.enhetsstreng}', "
            f"sn='{self._enhetsinfo.serienummer}')"
        )
