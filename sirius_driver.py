#!/usr/bin/env python3
"""
SIRIUS Hoeynivaa-driver (Lag 2)
=================================
Brukervenlig driver med tilkobling, initialisering, konfigurasjon og streaming.

Initialiserings-sekvensen er basert paa reverse-engineering av DewesoftX sin
USB-trafikk, fanget med usbmon paa Raspberry Pi.

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
    EP_CMD_IN, EP_ADC_IN, EP_CTRL_IN, EP_SYNC_IN,
    ALLE_SLOTTER,
    REG_CMD, REG_COMMIT, REG_SAMPLE_CFG,
    REG_TRIG_33, REG_TRIG_08, REG_TRIG_0B, REG_TRIG_0A,
    ADC_KANALER,
    SiriusFeil, SiriusUSBFeil, SiriusPollTimeout, SiriusIkkeFunnet,
    TIMEOUT_DATA,
)

log = logging.getLogger('sirius_driver')


# --- Dataklasser ---

@dataclass
class SlotInfo:
    """Informasjon om ein enkelt slot (analog inngangsmodul)."""
    slot_id: int = 0
    kanal_nummer: int = 0
    slot_type: int = 0        # 0x04=analog, 0x06=digital
    produsent: str = ""
    maskinvare_del: str = ""
    firmware_versjon: str = ""
    aktiv: bool = False
    kalibrering_raa: bytes = field(default_factory=bytes)


@dataclass
class EnhetsInfo:
    """Samla einingsinformasjon."""
    enhetsstreng: str = ""        # "DEWEUSB7"
    serienummer: str = ""
    fw_versjon: bytes = field(default_factory=bytes)
    slot_tilstedevaerelse: bytes = field(default_factory=bytes)
    slot_typer: bytes = field(default_factory=bytes)
    antall_slotter: int = 4
    slotter: list = field(default_factory=list)


@dataclass
class MaaleKonfig:
    """Konfigurasjon for ei maaling."""
    sample_rate: int = 1000
    aktive_kanaler: list = field(default_factory=lambda: list(range(ADC_KANALER)))
    varighet_sek: float = 5.0


class SiriusDriver:
    """
    Hoeynivaa-driver for Dewesoft SIRIUS.

    Haandterer tilkobling, initialisering, konfigurasjon og streaming.
    Initialiserings-sekvensen repliserer DewesoftX sin protokoll.
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
            SiriusIkkeFunnet: Hvis SIRIUS ikkje finst paa USB
            SiriusUSBFeil: Ved USB-feil
        """
        if usb is None:
            raise SiriusFeil("pyusb er ikkje installert (pip install pyusb)")

        log.info(f"Soeker etter SIRIUS (VID=0x{DEWESOFT_VID:04X}, PID=0x{SIRIUS_PID:04X})...")

        dev = usb.core.find(idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID)
        if dev is None:
            raise SiriusIkkeFunnet(
                f"SIRIUS ikkje funnet paa USB "
                f"(VID=0x{DEWESOFT_VID:04X}, PID=0x{SIRIUS_PID:04X})"
            )

        log.info(f"SIRIUS funnet: Bus {dev.bus}, Adresse {dev.address}")

        # VIKTIG: IKKJE gjer dev.reset() - det forstyrrer FX2-firmware og
        # gjer at enheten responderer med all-0xFF paa alle kommandoar.

        # Logg USB-deskriptorar for feilsoeking
        try:
            cfg = dev.get_active_configuration()
            if cfg:
                log.info(f"  USB-konfig: #{cfg.bConfigurationValue}, "
                         f"{cfg.bNumInterfaces} interface(s)")
                for intf in cfg:
                    log.info(f"  Interface {intf.bInterfaceNumber}: "
                             f"{intf.bNumEndpoints} endepunkt, "
                             f"klasse=0x{intf.bInterfaceClass:02X}")
            else:
                log.info("  Ingen aktiv USB-konfigurasjon")
        except Exception as e:
            log.debug(f"  Kunne ikkje lese USB-deskriptorar: {e}")

        # Frigjor kernel-driver KUN paa interface 0 (same som sirius_adc_leser.py
        # som les EP2 utan problem). Å detache alle interface kan forstyrre.
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
                log.info("Kernel-driver frigitt for interface 0")
        except (usb.core.USBError, NotImplementedError) as e:
            log.debug(f"detach_kernel_driver(0): {e}")

        # ALLTID kall set_configuration() - dette er kritisk for at EP2 skal
        # fungere. sirius_adc_leser.py gjer dette og les EP2 utan problem.
        # set_configuration() nullstiller interface-tilstandar internt i USB-stakken.
        try:
            dev.set_configuration()
            log.info("set_configuration OK")
        except usb.core.USBError as e:
            # "Resource busy" kan skje viss allereie konfigurert - det er OK
            if "Resource busy" in str(e) or "errno 16" in str(e).lower():
                log.info(f"set_configuration: allereie konfigurert ({e})")
            else:
                raise SiriusUSBFeil(f"set_configuration feilet: {e}") from e

        # Klaim interface 0 eksplisitt (alle 6 endepunkt er paa interface 0)
        try:
            usb.util.claim_interface(dev, 0)
            log.info("Interface 0 klaimet OK")
        except usb.core.USBError as e:
            log.warning(f"claim_interface(0): {e}")

        self._dev = dev
        self._proto = SiriusProtokoll(dev)
        self._tilkoblet = True
        self._rekoble_forsok = 0

        # --- Diagnostikk: Test EP2 FOER init ---
        log.info("Testar EP2 foer init...")
        try:
            test_ep2 = dev.read(EP_ADC_IN, 512, timeout=500)
            log.info(f"  EP2 pre-init: {len(test_ep2)} bytes OK!")
            self._ep2_pre_init_ok = True
        except Exception as e:
            log.warning(f"  EP2 pre-init feilet: {e}")
            self._ep2_pre_init_ok = False

        # Flush EP1 IN for aa fjerne gammal data fraa tidlegare session
        log.info("Flushar EP1 IN...")
        self._proto.flush_endepunkt(EP_CMD_IN, forsok=10)

        # Test tilkoblinga med AE foer full init
        log.info("Testar tilkobling med AE heartbeat...")
        try:
            test = self._proto.send_telemetri()
            all_ff = all(b == 0xFF for b in test)
            log.info(f"  AE test-svar: {test[:16].hex()} "
                     f"({'ALL-FF - eining responderer ikkje ennaa' if all_ff else 'OK'})")
            if all_ff:
                log.warning(
                    "Enheten gir all-0xFF svar. "
                    "Proever EP1 flush + ny AE etter kort pause..."
                )
                time.sleep(0.5)
                self._proto.flush_endepunkt(EP_CMD_IN, forsok=10)
                test2 = self._proto.send_telemetri()
                all_ff2 = all(b == 0xFF for b in test2)
                log.info(f"  AE test #2: {test2[:16].hex()} "
                         f"({'FRAMLEIS ALL-FF' if all_ff2 else 'OK'})")
        except SiriusUSBFeil as e:
            log.warning(f"  AE test feilet: {e}")

        # Kjoer full initialisering (repliserer DewesoftX)
        self._initialiser()

        # --- Diagnostikk: Test EP2 ETTER init ---
        log.info("Testar EP2 etter init...")
        try:
            test_ep2 = dev.read(EP_ADC_IN, 512, timeout=500)
            log.info(f"  EP2 post-init: {len(test_ep2)} bytes OK!")
        except Exception as e:
            log.warning(f"  EP2 post-init feilet: {e}")
            # Proev clear_halt for aa nullstille EP2
            log.info("  Proever clear_halt paa EP2...")
            try:
                dev.clear_halt(EP_ADC_IN)
                log.info("  clear_halt OK")
                test_ep2 = dev.read(EP_ADC_IN, 512, timeout=500)
                log.info(f"  EP2 etter clear_halt: {len(test_ep2)} bytes OK!")
            except Exception as e2:
                log.warning(f"  EP2 etter clear_halt feilet ogsaa: {e2}")

        log.info("SIRIUS tilkobla og initialisert")

    def koble_fra(self):
        """Stopp streaming og frigjor USB-enhet."""
        if self._streamer:
            self.stopp_streaming()

        self._tilkoblet = False

        if self._dev is not None:
            # Release interface 0 (auto-klaimet av pyusb ved I/O)
            try:
                usb.util.release_interface(self._dev, 0)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self._dev)
            except Exception as e:
                log.debug(f"Feil ved frigjoring av USB: {e}")
            self._dev = None
            self._proto = None

        log.info("SIRIUS frakobla")

    def er_tilkoblet(self) -> bool:
        """Sjekk om eininga er tilkobla (kun tilstandssjekk, ingen USB I/O)."""
        return self._tilkoblet and self._dev is not None

    def rekoble(self) -> bool:
        """Proev aa koble til paa nytt med rein USB-tilstand."""
        log.info("Proever aa rekoble til SIRIUS...")
        try:
            self.koble_fra()
            time.sleep(0.5)
            self.koble_til()
            return True
        except SiriusFeil as e:
            self._rekoble_forsok += 1
            log.error(f"Rekonnektering feilet ({self._rekoble_forsok}/{self._maks_rekoble}): {e}")
            return False

    # ---- Initialisering (repliserer DewesoftX-sekvensen) ----

    def _initialiser(self):
        """
        Kjoer full init-sekvens basert paa reverse-engineered DewesoftX-protokoll.

        Fase 1: Heartbeat-sjekk (AE telemetri x4)
        Fase 2: Enhetsoppdaging (FW-versjon, modus, slotinfo, EEPROM, init)
        Fase 3: Slot-enumerering (AD query, enum, batch)
        Fase 4: Per-slot initialisering (A5 kommando-dispatch)
        Fase 5: Flush dataendepunkt
        """
        log.info("Initialiserer SIRIUS (DewesoftX-sekvens)...")
        proto = self._proto

        # ---- Fase 1: Heartbeat-sjekk ----
        log.info("  Fase 1: Heartbeat-sjekk...")
        alle_ff_teller = 0
        for i in range(4):
            try:
                svar = proto.send_telemetri()
                er_ff = all(b == 0xFF for b in svar)
                if er_ff:
                    alle_ff_teller += 1
                log.info(f"    AE #{i+1}: {svar[:8].hex()} "
                         f"({len(svar)}B{' ALL-FF' if er_ff else ''})")
            except SiriusUSBFeil as e:
                log.warning(f"    AE #{i+1} feilet: {e}")

        if alle_ff_teller >= 4:
            log.error(
                "ALLE AE-svar er 0xFF! Enheten responderer ikkje paa kommandoar. "
                "Mogelege aarsaker: "
                "1) FX2-firmware ikkje lasta / korrumpert, "
                "2) USB-reset har forstyrra firmware, "
                "3) Enheten treng straumsykling (koble fraa/til USB-kabelen)"
            )

        # ---- Fase 2: Enhetsoppdaging ----
        log.info("  Fase 2: Enhetsoppdaging...")

        # FW-versjon (0x00)
        try:
            fw = proto.les_fw_versjon()
            self._enhetsinfo.fw_versjon = fw
            er_ff = all(b == 0xFF for b in fw)
            log.info(f"    FW-versjon: {fw[:8].hex()} ({len(fw)}B{' ALL-FF' if er_ff else ''})")
        except SiriusUSBFeil as e:
            log.warning(f"    FW-versjon feilet: {e}")

        # Aktiver eining (A0 01)
        try:
            proto.sett_aktiv_modus()
        except SiriusUSBFeil as e:
            log.warning(f"    Sett aktiv modus feilet: {e}")

        # Slot-tilstedevaerelse (A1)
        try:
            slot_map = proto.hent_slot_tilstedevaerelse()
            self._enhetsinfo.slot_tilstedevaerelse = slot_map
            er_ff = all(b == 0xFF for b in slot_map)
            log.info(f"    Slot-kart: {slot_map[:16].hex()} ({len(slot_map)}B{' ALL-FF' if er_ff else ''})")
        except SiriusUSBFeil as e:
            log.warning(f"    Slot-tilstedevaerelse feilet: {e}")

        # Slot-typer (AC)
        try:
            slot_types = proto.hent_slot_typer()
            self._enhetsinfo.slot_typer = slot_types
            er_ff = all(b == 0xFF for b in slot_types)
            log.info(f"    Slot-typer: {slot_types[:16].hex()} ({len(slot_types)}B{' ALL-FF' if er_ff else ''})")
        except SiriusUSBFeil as e:
            log.warning(f"    Slot-typer feilet: {e}")

        # EEPROM-lesing (A8) - enhetsstreng og serienummer
        try:
            eeprom = proto.les_eeprom(0x00, 0x00)
            er_ff = all(b == 0xFF for b in eeprom)
            log.info(f"    EEPROM raa: {eeprom[:16].hex()} ({len(eeprom)}B{' ALL-FF' if er_ff else ''})")
            if not er_ff:
                tekst = eeprom.rstrip(b'\x00\xff').decode('ascii', errors='replace')
                if tekst:
                    self._enhetsinfo.enhetsstreng = tekst[:8]
                    self._enhetsinfo.serienummer = tekst[8:].strip('\x00').strip()
                    log.info(f"    Eining: {self._enhetsinfo.enhetsstreng}")
                    log.info(f"    Serienr: {self._enhetsinfo.serienummer}")
        except SiriusUSBFeil as e:
            log.warning(f"    EEPROM feilet: {e}")
            # Fallback: proev E3 (FX2-lag)
            try:
                ident = proto.les_enhetsid()
                self._enhetsinfo.enhetsstreng = ident.get('enhetsstreng', '')
                self._enhetsinfo.serienummer = ident.get('serienummer', '')
                log.info(f"    Eining (E3): {self._enhetsinfo.enhetsstreng}")
                log.info(f"    Serienr (E3): {self._enhetsinfo.serienummer}")
            except SiriusUSBFeil:
                pass

        # Init-kommando (B0 3F 0C)
        try:
            proto.init_kommando()
        except SiriusUSBFeil as e:
            log.warning(f"    Init-kommando feilet: {e}")

        # ---- Fase 3: Slot-enumerering ----
        log.info("  Fase 3: Slot-enumerering...")

        try:
            svar = proto.slot_query()
            log.info(f"    Slot-query: {svar[:12].hex()}")
        except (SiriusPollTimeout, SiriusUSBFeil) as e:
            log.warning(f"    Slot-query feilet: {e}")

        for enum_slot in [0x00, 0x01, 0x02]:
            try:
                svar = proto.slot_enum(enum_slot)
                log.debug(f"    Enum slot {enum_slot}: {svar[:12].hex()}")
            except (SiriusPollTimeout, SiriusUSBFeil) as e:
                log.debug(f"    Enum slot {enum_slot} feilet: {e}")

        try:
            svar = proto.batch_op()
            log.info(f"    Batch-op: {svar[:12].hex()}")
        except (SiriusPollTimeout, SiriusUSBFeil) as e:
            log.warning(f"    Batch-op feilet: {e}")

        # ---- Fase 4: Per-slot initialisering ----
        log.info("  Fase 4: Per-slot initialisering...")
        self._enhetsinfo.slotter = []

        for i, slot_addr in enumerate(ALLE_SLOTTER):
            slot = SlotInfo(slot_id=slot_addr, kanal_nummer=i)

            # Sjekk slot-type fraa kartet
            if (self._enhetsinfo.slot_typer
                    and len(self._enhetsinfo.slot_typer) > i
                    and self._enhetsinfo.slot_typer[i] > 0):
                slot.slot_type = self._enhetsinfo.slot_typer[i]

            try:
                self._init_slot(slot)
                slot.aktiv = True
                log.info(f"    Slot {i} (0x{slot_addr:02X}): OK - {slot.produsent}")
            except (SiriusPollTimeout, SiriusUSBFeil) as e:
                log.warning(f"    Slot {i} (0x{slot_addr:02X}): feilet ({e})")
                slot.aktiv = False

            self._enhetsinfo.slotter.append(slot)

        aktive = sum(1 for s in self._enhetsinfo.slotter if s.aktiv)
        log.info(f"    Aktive slotter: {aktive}/{len(ALLE_SLOTTER)}")

        # ---- Fase 5: Flush dataendepunkt ----
        log.info("  Fase 5: Flush endepunkt...")
        for ep in [EP_ADC_IN, EP_CTRL_IN, EP_SYNC_IN]:
            self._proto.flush_endepunkt(ep)

        log.info("Initialisering fullfoert")

    def _init_slot(self, slot: SlotInfo):
        """
        Initialiser ein enkelt analog slot via A5 kommando-dispatch.

        Repliserer DewesoftX sin per-slot sekvens:
        1. Aktiver slot (A5 SET_PARAM 0xF0)
        2. Sett modus (A5 SET_MODE D1=01 + commit)
        3. Les konfigurasjon (A5 READ_STRING D1,02 + trigger 0x33)
        4. Les kalibrering (A5 READ_STRING D1,03 + trigger 0x08)
        5. Sett modus paa nytt + utvidet konfig
        6. Samplingsoppsett (CC register)
        7. Les modulstrenger (trigger 0x0B)
        8. Les binaerdata (trigger 0x0A)
        """
        proto = self._proto
        s = slot.slot_id

        # 1. Aktiver slot
        proto.a5_set_param(s, 0xF0)
        proto.les_register(s)

        # 2. Sett modus D1=01 + commit
        proto.a5_set_mode(s, 0xD1, 0x01)
        proto.commit(s)
        proto.les_register(s)

        # 3. Les konfigurasjon (D1 offset 0x02 via trigger 0x33)
        proto.a5_read_string(s, 0xD1, 0x02)
        proto.trigger_read(s, REG_TRIG_33)
        proto.les_register(s)
        proto.les_register(s)  # Andre lesing for komplett data

        # 4. Les kalibrering (D1 offset 0x03 via trigger 0x08)
        proto.a5_read_string(s, 0xD1, 0x03)
        proto.trigger_read(s, REG_TRIG_08)
        kalibrering = proto.les_register(s)
        slot.kalibrering_raa = kalibrering
        proto.les_register(s)  # Andre lesing

        # 5. Sett modus paa nytt + commit
        proto.a5_set_mode(s, 0xD1, 0x01)
        proto.commit(s)
        proto.les_register(s)

        # 6. Utvidet konfig + samplingsoppsett
        proto.a5_set_config(s, 0xD1, 0x02)
        proto.skriv_register(s, REG_SAMPLE_CFG, bytes([0xF0, 0x00, 0x00]))
        proto.commit(s)
        proto.les_register(s)

        # 7. Les modulstrenger via trigger 0x0B (produsent, serienr)
        for offset in [0x02, 0x03]:
            proto.a5_read_string(s, 0xD1, 0x03)
            proto.trigger_read(s, REG_TRIG_0B)
            svar = proto.les_register(s)
            # Parse streng fraa svar
            if offset == 0x02:
                self._parse_slot_streng(slot, svar, 'produsent')
            elif offset == 0x03:
                self._parse_slot_streng(slot, svar, 'maskinvare_del')

        # 8. Les binaerdata via trigger 0x0A
        proto.a5_read_string(s, 0xD1, 0x03)
        proto.trigger_read(s, REG_TRIG_0A)
        proto.les_register(s)

    def _parse_slot_streng(self, slot, svar, felt):
        """Parse ein tekststreng fraa slot-svar og sett paa SlotInfo."""
        try:
            # Hopp over status-bytes og finn ASCII-tekst
            tekst = ""
            for b in svar:
                if 0x20 <= b <= 0x7E:
                    tekst += chr(b)
                elif tekst:
                    break
            if tekst:
                setattr(slot, felt, tekst.strip())
        except Exception:
            pass

    # ---- Streaming ----

    def start_streaming(self, callback=None, buffer_storrelse=1000):
        """Start ADC-datastreaming i bakgrunnstraader."""
        if self._streamer:
            log.warning("Streaming kjoerer allereie")
            return

        if not self._tilkoblet:
            raise SiriusFeil("Ikkje tilkobla - kall koble_til() foerst")

        self._data_callback = callback
        self._buffer_storrelse = buffer_storrelse
        self._stopp_event.clear()
        self._streamer = True
        self._data_rate_bytes = 0
        self._data_rate_ts = time.time()

        # Nullstill EP2 foer lesing
        try:
            self._dev.clear_halt(EP_ADC_IN)
            log.info("EP2 clear_halt OK")
        except Exception as e:
            log.debug(f"EP2 clear_halt: {e}")

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

        log.info("Streaming starta")

    def stopp_streaming(self):
        """Stopp streaming og vent paa at traader avslutter."""
        if not self._streamer:
            return

        log.info("Stoppar streaming...")
        self._stopp_event.set()
        self._streamer = False

        current = threading.current_thread()
        if self._adc_traad and self._adc_traad.is_alive() and self._adc_traad is not current:
            self._adc_traad.join(timeout=5)
        if self._heartbeat_traad and self._heartbeat_traad.is_alive() and self._heartbeat_traad is not current:
            self._heartbeat_traad.join(timeout=5)

        self._adc_traad = None
        self._heartbeat_traad = None
        log.info("Streaming stoppa")

    def hent_data(self, antall_rammer=None):
        """Hent buffra data."""
        with self._buffer_lock:
            if antall_rammer is None:
                data = list(self._data_buffer)
                self._data_buffer.clear()
            else:
                data = self._data_buffer[:antall_rammer]
                self._data_buffer = self._data_buffer[antall_rammer:]
        return data

    def _adc_leser_loop(self):
        """Bakgrunnstraad: les ADC-data fraa EP2 og parser.

        Brukar 512 bytes per lesing (same som sirius_adc_leser.py som fungerer).
        Stoeerre lesingar (16384) kan foraarsake EBUSY paa nokre USB-stakkar.
        """
        io_feil_teller = 0

        while not self._stopp_event.is_set():
            try:
                raa = self._proto.les_adc_data(
                    storrelse=512,
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

                    # Parser 8-kanal int16-data
                    kanal_data = self._deinterlev_data(raa, ADC_KANALER)

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

                io_feil_teller = 0

            except SiriusUSBFeil as e:
                if self._stopp_event.is_set():
                    break

                feil_str = str(e).lower()
                if "timeout" in feil_str or "timed out" in feil_str:
                    # Timeout er normalt naar ADC ikkje er aktiv
                    log.debug("ADC timeout (ventar paa data)")
                    self._stopp_event.wait(timeout=0.5)
                    continue

                # Ekte I/O-feil (Errno 5, Errno 16 etc.)
                io_feil_teller += 1
                log.warning(f"ADC I/O-feil ({io_feil_teller}): {e}")

                # Ved EBUSY: proev clear_halt for aa nullstille endepunktet
                if "errno 16" in str(e).lower() or "resource busy" in str(e).lower():
                    try:
                        self._dev.clear_halt(EP_ADC_IN)
                        log.info("EP2 clear_halt etter EBUSY")
                    except Exception:
                        pass

                if io_feil_teller >= 10:
                    log.error(
                        "For mange ADC I/O-feil - stoppar streaming. "
                        "Bruk Rekoble + Start streaming fraa web UI."
                    )
                    self._streamer = False
                    break

                # Kort pause foer retry
                self._stopp_event.wait(timeout=1.0)

            except Exception as e:
                if self._stopp_event.is_set():
                    break
                log.error(f"Uventa feil i ADC-loop: {e}")
                self._stopp_event.wait(timeout=1.0)

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
    def _deinterlev_data(raa_bytes, antall_kanaler=ADC_KANALER):
        """
        Split interleaved int16-data til per-kanal arrays.

        EP2 format: 2 rammer x 8 kanaler x int16 LE = 32 bytes per pakke.
        Ved stoeerre buffer (16384 bytes): 512 pakker x 2 rammer = 1024 rammer.
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

    # ---- Info-metodar ----

    def hent_status(self) -> dict:
        """Returner driver-status som dict (for API/web)."""
        return {
            "tilkoblet": self._tilkoblet,
            "streamer": self._streamer,
            "enhetsstreng": self._enhetsinfo.enhetsstreng,
            "serienummer": self._enhetsinfo.serienummer,
            "enhetstype": self._enhetsinfo.fw_versjon.hex() if self._enhetsinfo.fw_versjon else "",
            "antall_slotter": self._enhetsinfo.antall_slotter,
            "aktive_slotter": sum(1 for s in self._enhetsinfo.slotter if s.aktiv),
            "slotter": [
                {
                    "slot_id": f"0x{s.slot_id:02X}",
                    "kanal": s.kanal_nummer,
                    "slot_type": f"0x{s.slot_type:02X}" if s.slot_type else "",
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
            f"eining='{self._enhetsinfo.enhetsstreng}', "
            f"sn='{self._enhetsinfo.serienummer}')"
        )
