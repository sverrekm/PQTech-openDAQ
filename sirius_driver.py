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
import subprocess
import threading
import logging
from pathlib import Path
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
    OPCODE_SETMODE, OPCODE_INIT, OPCODE_PRESTART,
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
        self._ep2_ok = False
        self._treng_heartbeat = False  # Sett True etter _start_acquisition()

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
    def ep2_ok(self) -> bool:
        """True viss EP2 (ADC-data) svarte ved tilkobling."""
        return self._ep2_ok

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

        1. usb.core.find() + detach_kernel_driver + set_configuration
        2. Test EP2 direkte (ADC-data)
        3. Viss EP2 ikkje svarer: køyr start-acquisition automatisk
        4. Viss start-acquisition feilar: prøv init + start-acquisition

        SIRIUS treng start-acquisition-sekvensen (register 0x02 trigger)
        for å aktivere EP2 ADC-streaming.

        Raises:
            SiriusIkkeFunnet: Hvis SIRIUS ikkje finst paa USB
            SiriusUSBFeil: Ved USB-feil
        """
        if usb is None:
            raise SiriusFeil("pyusb er ikkje installert (pip install pyusb)")

        self._koble_til_intern()

        if not self._ep2_ok:
            # EP2 ikkje aktiv etter USB-enumerering.  Køyr start-acquisition
            # automatisk — SIRIUS treng dette for å aktivere ADC-streaming.
            log.info("EP2 ikkje aktiv — køyrer start-acquisition automatisk...")
            try:
                self._start_acquisition()
                time.sleep(0.5)
                if self._test_ep2():
                    log.info("EP2 starta etter automatisk start-acquisition")
                else:
                    log.warning("EP2 svarte ikkje etter start-acquisition — prøver init + start...")
                    # Strategi 2: full init + start-acquisition
                    try:
                        self._initialiser()
                        time.sleep(0.5)
                        self._start_acquisition()
                        time.sleep(0.5)
                        if self._test_ep2():
                            log.info("EP2 starta etter init + start-acquisition")
                    except Exception as e2:
                        log.warning(f"Init + start-acquisition feilet: {e2}")
            except Exception as e:
                log.warning(f"Automatisk start-acquisition feilet: {e}")

        if not self._ep2_ok:
            log.warning(
                "EP2 (ADC) svarte ikkje - bruk 'Gjenoppliv EP2' i web UI."
            )

    def _frigjer_dev(self):
        """Frigjer USB-handle utan full koble_fra (unngår stopp_streaming)."""
        if self._dev is not None:
            try:
                usb.util.release_interface(self._dev, 0)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self._dev)
            except Exception:
                pass
            self._dev = None
            self._proto = None
            self._tilkoblet = False

    def _koble_til_intern(self):
        """Intern tilkoblingslogikk (find → detach → configure → test EP2)."""
        log.info(f"Soeker etter SIRIUS (VID=0x{DEWESOFT_VID:04X}, PID=0x{SIRIUS_PID:04X})...")

        # Vent til udev har prosessert USB-endringar slik at device-noder
        # finst i /dev/bus/usb/.  Nødvendig etter USB reset/power-cycle
        # der devicet re-enumererer med ny bus-adresse.
        try:
            subprocess.run(
                ["udevadm", "settle", "--timeout=5"],
                capture_output=True, timeout=8,
            )
        except Exception:
            pass  # udevadm ikkje tilgjengeleg — held fram

        dev = usb.core.find(idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID)
        if dev is None:
            raise SiriusIkkeFunnet(
                f"SIRIUS ikkje funnet paa USB "
                f"(VID=0x{DEWESOFT_VID:04X}, PID=0x{SIRIUS_PID:04X})"
            )

        log.info(f"SIRIUS funnet: Bus {dev.bus}, Adresse {dev.address}")

        # 1. Detach kernel driver paa interface 0
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
                log.info("  Kernel-driver frigitt for interface 0")
        except (usb.core.USBError, NotImplementedError) as e:
            log.debug(f"  detach_kernel_driver(0): {e}")

        # 2. set_configuration()
        try:
            dev.set_configuration()
            log.info("  set_configuration() OK")
        except usb.core.USBError as e:
            if "Resource busy" in str(e) or "errno 16" in str(e).lower():
                log.info("  set_configuration EBUSY - frigjor interface og prøver igjen")
                try:
                    usb.util.release_interface(dev, 0)
                except Exception:
                    pass
                time.sleep(0.3)
                try:
                    dev.set_configuration()
                    log.info("  set_configuration() OK (etter release)")
                except usb.core.USBError as e2:
                    if "Resource busy" in str(e2) or "errno 16" in str(e2).lower():
                        log.info("  set_configuration: framleis busy, held fram likevel")
                    else:
                        raise SiriusUSBFeil(f"set_configuration feilet: {e2}") from e2
            else:
                raise SiriusUSBFeil(f"set_configuration feilet: {e}") from e

        self._dev = dev
        self._proto = SiriusProtokoll(dev)
        self._tilkoblet = True
        self._rekoble_forsok = 0

        # Hent device-metadata frå USB string descriptors
        self._les_usb_metadata(dev)

        # 3. Test EP2 direkte (5s timeout for trege device)
        log.info("Testar EP2 (ADC-data)...")
        self._ep2_ok = False
        try:
            test_ep2 = dev.read(EP_ADC_IN, 512, timeout=5000)
            log.info(f"  EP2 OK: {len(test_ep2)} bytes lest")
            self._ep2_ok = True
        except usb.core.USBError as e:
            log.warning(f"  EP2 test feilet: {e}")

        log.info(f"SIRIUS tilkobla (EP2: {'OK' if self._ep2_ok else 'FEIL'})")

    def _les_usb_metadata(self, dev):
        """Les device-metadata frå USB string descriptors.

        Dette krev IKKJE init-sekvensen og forstyrrar ikkje EP2.
        USB string descriptors er standard USB-funksjonalitet.
        """
        try:
            produsent = usb.util.get_string(dev, dev.iManufacturer) if dev.iManufacturer else ""
            produkt = usb.util.get_string(dev, dev.iProduct) if dev.iProduct else ""
            serienr = usb.util.get_string(dev, dev.iSerialNumber) if dev.iSerialNumber else ""
            log.info(f"  Produsent: {produsent or '(ikkje tilgjengeleg)'}")
            log.info(f"  Produkt:   {produkt or '(ikkje tilgjengeleg)'}")
            log.info(f"  Serienr:   {serienr or '(ikkje tilgjengeleg)'}")
            self._enhetsinfo.enhetsstreng = produkt or produsent or "SIRIUS"
            self._enhetsinfo.serienummer = serienr or ""
        except Exception as e:
            log.debug(f"  Kunne ikkje lese USB-strengar: {e}")
            self._enhetsinfo.enhetsstreng = "SIRIUS"
            self._enhetsinfo.serienummer = ""

    @staticmethod
    def _finn_sysfs_sti():
        """Finn sysfs-stien til SIRIUS USB-devicet.

        Søker i /sys/bus/usb/devices/ etter VID=1ced, PID=1002.
        Returns: Path eller None
        """
        sysfs_base = Path("/sys/bus/usb/devices")
        if not sysfs_base.exists():
            return None
        for dev_dir in sysfs_base.iterdir():
            try:
                vid_file = dev_dir / "idVendor"
                pid_file = dev_dir / "idProduct"
                if vid_file.exists() and pid_file.exists():
                    vid = vid_file.read_text().strip()
                    pid = pid_file.read_text().strip()
                    if vid == "1ced" and pid == "1002":
                        return dev_dir
            except (OSError, PermissionError):
                continue
        return None

    @staticmethod
    def sysfs_usb_reset():
        """Soft-reset SIRIUS via sysfs authorized-flag.

        Ekvivalent med fysisk USB-replug - deauthorize → reauthorize.
        Krev privilegert tilgang og /sys montert i containeren.

        Returns: True viss reset vart utført
        """
        dev_dir = SiriusDriver._finn_sysfs_sti()
        if dev_dir is None:
            log.warning("sysfs: Fann ikkje SIRIUS i /sys/bus/usb/devices/")
            return False

        auth_file = dev_dir / "authorized"
        if not auth_file.exists():
            log.warning(f"sysfs: {auth_file} finst ikkje")
            return False

        try:
            log.info(f"sysfs USB power-cycle: {dev_dir.name}")
            # Deauthorize (koble frå)
            auth_file.write_text("0")
            log.info("  sysfs: deauthorized (USB fråkopla)")
            time.sleep(2)
            # Reauthorize (koble til att)
            auth_file.write_text("1")
            log.info("  sysfs: reauthorized (USB tilkopla)")
            time.sleep(3)  # Gi FX2 firmware tid til å starte
            return True
        except Exception as e:
            log.error(f"sysfs USB reset feilet: {e}")
            return False

    def _test_ep2(self, timeout=3000):
        """Rask test om EP2 leverer data.

        Returns: True viss EP2 svarte med data
        """
        try:
            data = self._dev.read(EP_ADC_IN, 512, timeout=timeout)
            if data and len(data) > 0:
                log.info(f"  EP2 test OK: {len(data)} bytes")
                self._ep2_ok = True
                return True
        except Exception as e:
            log.debug(f"  EP2 test feilet: {e}")
        return False

    def _usb_bus_reset(self):
        """Send USB RESET-signal via dev.reset().

        Dette tvingar FX2 USB-kontrollaren til å reboote firmware.
        FUNDAMENTALT FORSKJELLIG frå sysfs authorized-toggle:
        - sysfs: kernel drop/reacquire (FX2 får framleis straum, rebootter ikkje)
        - dev.reset(): sender USB RESET-signal på bussen → FX2 firmware rebootter

        Viss FX2 firmware sin oppstartsrutine sender "start stream" til
        hovudkontrollaren, vil dette restarte EP2.

        Etter reset er device-handle ugyldig og må finnast på nytt.
        """
        if self._dev is None:
            log.warning("  dev.reset: ingen device-handle")
            return False

        try:
            log.info("  Sender USB RESET-signal (dev.reset)...")
            self._dev.reset()
            log.info("  USB RESET sendt - ventar på FX2 reboot...")
            time.sleep(4)
            return True
        except usb.core.USBError as e:
            # Forventa: device disconnects under reset
            log.info(f"  USB RESET: {e} (forventa under reboot)")
            time.sleep(4)
            return True
        except Exception as e:
            log.warning(f"  USB RESET feilet: {e}")
            return False

    @staticmethod
    def _uhubctl_power_cycle():
        """Power-cycle USB-porten via uhubctl.

        Kuttar fysisk straum til USB-porten og slaar den paa att.
        Dette resetter BEGGE FX2 OG hovudkontrollaren viss SIRIUS
        får straum frå USB (ikkje ekstern PSU).

        Krev uhubctl installert og at USB-hubben støttar power switching.
        """
        # Finn SIRIUS device bus og port fraa sysfs
        dev_dir = SiriusDriver._finn_sysfs_sti()
        if dev_dir is None:
            log.warning("  uhubctl: Fann ikkje SIRIUS i sysfs")
            return False

        # Parse bus-port frå sysfs-sti (t.d. "3-2" → bus=3, port=2)
        dev_name = dev_dir.name
        parts = dev_name.split("-")
        if len(parts) < 2:
            log.warning(f"  uhubctl: Kan ikkje parse sysfs-sti '{dev_name}'")
            return False

        bus = parts[0]
        port = parts[1].split(".")[0]  # Handter multi-nivaa som "3-2.1"

        # Sjekk om uhubctl er tilgjengeleg
        try:
            result = subprocess.run(
                ["uhubctl", "--version"],
                capture_output=True, timeout=5
            )
            version = result.stdout.decode().strip() or result.stderr.decode().strip()
            log.info(f"  uhubctl tilgjengeleg: {version[:60]}")
        except FileNotFoundError:
            log.warning("  uhubctl ikkje installert")
            return False
        except subprocess.TimeoutExpired:
            log.warning("  uhubctl timeout")
            return False

        try:
            # Power OFF
            log.info(f"  uhubctl: power OFF bus {bus} port {port}...")
            off_result = subprocess.run(
                ["uhubctl", "-l", bus, "-p", port, "-a", "off"],
                capture_output=True, timeout=10
            )
            off_output = off_result.stdout.decode() + off_result.stderr.decode()
            if "does not support" in off_output.lower() or "no compatible" in off_output.lower():
                log.warning(f"  uhubctl: Hub støttar ikkje power switching")
                log.warning(f"  uhubctl output: {off_output.strip()[:200]}")
                return False
            log.info(f"  uhubctl: port {port} power OFF")

            time.sleep(3)

            # Power ON
            log.info(f"  uhubctl: power ON bus {bus} port {port}...")
            subprocess.run(
                ["uhubctl", "-l", bus, "-p", port, "-a", "on"],
                capture_output=True, timeout=10
            )
            log.info(f"  uhubctl: port {port} power ON - ventar på enumerering (8s)...")
            time.sleep(8)  # Lenger vent: FX2 treng tid til firmware-lasting

            # Trigger udev re-skanning og vent til device-noder er klare
            try:
                subprocess.run(
                    ["udevadm", "trigger", "--action=add", "--subsystem-match=usb"],
                    capture_output=True, timeout=5,
                )
                subprocess.run(
                    ["udevadm", "settle", "--timeout=5"],
                    capture_output=True, timeout=8,
                )
                log.info("  udevadm trigger+settle fullført")
            except Exception:
                pass  # udevadm ikkje tilgjengeleg — held fram

            return True
        except subprocess.TimeoutExpired:
            log.warning("  uhubctl: kommando timeout")
            # Forsøk å slaa porten paa att uansett
            try:
                subprocess.run(
                    ["uhubctl", "-l", bus, "-p", port, "-a", "on"],
                    capture_output=True, timeout=10
                )
            except Exception:
                pass
            time.sleep(3)
            return False
        except Exception as e:
            log.warning(f"  uhubctl feilet: {e}")
            return False

    def forsok_gjenoppliv_ep2(self):
        """
        Forsoek aa gjenopplive EP2 ADC-streaming.

        EP2 kan ha blitt stoppa av ein tidlegare init-sekvens.
        SIRIUS hovudkontrollar har eigen straumforsyning og overlever
        USB-fråkopling, saa tilstanden "EP2 stoppa" heng att.

        VIKTIG: Stoppar streaming fyrst for aa frigjoere EP2 fraa leser-traaden.

        Strategiar (i prioritert rekkjefylgje):
        1: Start-acquisition sekvens (DewesoftX-replika, fraa pcapng-analyse)
        2: Init + start-acquisition (full DewesoftX-syklus)
        3: dev.reset() + start-acquisition
        4: uhubctl power-cycle + start-acquisition

        Returns: True viss EP2 vart gjenoppliva
        """
        # Stopp streaming FYRST - leser-traaden held EP2 oppteken
        if self._streamer:
            log.info("Stoppar streaming foer EP2-gjenoppliving...")
            self.stopp_streaming()

        log.info("=" * 60)
        log.info("EP2 GJENOPPLIVING - Start Acquisition sekvens")
        log.info("  (reverse-engineered fraa DewesoftX pcapng 2026-02-14)")
        log.info("=" * 60)

        # ============================================================
        # STRATEGI 1: Start-acquisition sekvens direkte
        # Sender dei 35 register-kommandoane som DewesoftX brukar
        # for aa starte EP2 ADC-streaming (reg 0x02 trigger).
        # ============================================================
        if not self._tilkoblet or self._proto is None:
            try:
                self._koble_til_intern()
            except Exception as e:
                log.error(f"Kan ikkje koble til: {e}")
                return False

        log.info("Strategi 1/4: Start-acquisition sekvens (direkte)...")
        try:
            self._start_acquisition()
            time.sleep(0.5)
            if self._test_ep2():
                log.info("SUKSESS: Strategi 1 (start-acquisition)")
                return True
            log.info("  Start-acquisition sendt, men EP2 svarte ikkje enno")
        except (SiriusPollTimeout, SiriusUSBFeil) as e:
            log.warning(f"  Strategi 1 feilet: {e}")

        # ============================================================
        # STRATEGI 2: Init-sekvens + start-acquisition
        # Kjoer full DewesoftX-syklus: init fyrst (A0/A1/A8/B0/AD),
        # deretter start-acquisition. Init drep EP2, men start-
        # acquisition bringer den tilbake.
        # ============================================================
        log.info("Strategi 2/4: Init + start-acquisition (full DewesoftX-syklus)...")
        try:
            self._initialiser()
            time.sleep(0.5)
            self._start_acquisition()
            time.sleep(0.5)
            if self._test_ep2():
                log.info("SUKSESS: Strategi 2 (init + start-acquisition)")
                return True
            log.info("  Init + start sendt, men EP2 svarte ikkje")
        except (SiriusPollTimeout, SiriusUSBFeil) as e:
            log.warning(f"  Strategi 2 feilet: {e}")

        # ============================================================
        # STRATEGI 3: dev.reset() (USB bus reset) + start-acquisition
        # Reset FX2 firmware, koble til att, kjoer start-sekvens.
        # ============================================================
        log.info("Strategi 3/4: dev.reset() + start-acquisition...")
        try:
            if self._usb_bus_reset():
                self._frigjer_dev()
                time.sleep(1)
                self._koble_til_intern()
                if self._ep2_ok:
                    log.info("SUKSESS: Strategi 3a (dev.reset åleine)")
                    return True
                # EP2 ikkje oppe etter reset - proev start-sekvens
                try:
                    self._start_acquisition()
                    time.sleep(0.5)
                    if self._test_ep2():
                        log.info("SUKSESS: Strategi 3b (dev.reset + start-acquisition)")
                        return True
                except (SiriusPollTimeout, SiriusUSBFeil) as e:
                    log.warning(f"  Start-acquisition etter reset feilet: {e}")
        except Exception as e:
            log.warning(f"  Strategi 3 feilet: {e}")
            try:
                self._koble_til_intern()
            except Exception:
                pass

        # ============================================================
        # STRATEGI 4: uhubctl power-cycle + start-acquisition
        # Fysisk USB-straumkutt, koble til att, kjoer start-sekvens.
        # ============================================================
        log.info("Strategi 4/4: uhubctl + start-acquisition...")
        try:
            self._frigjer_dev()
            if self._uhubctl_power_cycle():
                for forsok in range(3):
                    try:
                        self._koble_til_intern()
                        break
                    except SiriusIkkeFunnet:
                        log.info(f"  uhubctl: enhet ikkje funne enno (forsoek {forsok+1}/3)...")
                        time.sleep(3)
                    except Exception as e:
                        log.warning(f"  uhubctl tilkobling forsoek {forsok+1}: {e}")
                        time.sleep(2)
                if self._ep2_ok:
                    log.info("SUKSESS: Strategi 4a (uhubctl åleine)")
                    return True
                # EP2 ikkje oppe - proev start-sekvens
                if self._tilkoblet and self._proto:
                    try:
                        self._start_acquisition()
                        time.sleep(0.5)
                        if self._test_ep2():
                            log.info("SUKSESS: Strategi 4b (uhubctl + start-acquisition)")
                            return True
                    except (SiriusPollTimeout, SiriusUSBFeil) as e:
                        log.warning(f"  Start-acquisition etter uhubctl feilet: {e}")
        except Exception as e:
            log.warning(f"  Strategi 4 feilet: {e}")
            try:
                self._koble_til_intern()
            except Exception:
                pass

        log.error("=" * 60)
        log.error("ALLE EP2-strategiar feilet (4/4).")
        log.error("Klikk Rekoble for å prøve med fersk USB-tilkopling.")
        log.error("=" * 60)

        # Sørg for rein tilstand: frigjer eventuelt stale USB-handle
        # slik at neste koble_til() / rekoble() startar friskt.
        self._frigjer_dev()
        return False

    def koble_fra(self):
        """Stopp streaming og frigjor USB-enhet.

        Tre-stegs frigjering for å unngå EBUSY ved neste tilkobling:
        1. release_interface(0) - frigjer pyusb auto-claim
        2. attach_kernel_driver(0) - gir interface tilbake til kernel
        3. dispose_resources() - frigjer backend-ressursar
        """
        if self._streamer:
            self.stopp_streaming()

        self._tilkoblet = False

        if self._dev is not None:
            # Steg 1: Release interface 0 (auto-klaimet av pyusb ved I/O)
            try:
                usb.util.release_interface(self._dev, 0)
                log.debug("  release_interface(0) OK")
            except Exception:
                pass

            # Steg 2: Re-attach kernel driver for å tvinge full frigjering
            # Utan dette held Linux-kernelen interfacet "busy" og neste
            # tilkobling får EBUSY på set_configuration() og EP2-lesingar.
            try:
                self._dev.attach_kernel_driver(0)
                log.debug("  attach_kernel_driver(0) OK")
            except (usb.core.USBError, NotImplementedError):
                pass

            # Steg 3: Frigjer backend-ressursar
            try:
                usb.util.dispose_resources(self._dev)
            except Exception as e:
                log.debug(f"  dispose_resources: {e}")

            self._dev = None
            self._proto = None

        log.info("SIRIUS frakobla")

    def er_tilkoblet(self) -> bool:
        """Sjekk om eininga er tilkobla (kun tilstandssjekk, ingen USB I/O)."""
        return self._tilkoblet and self._dev is not None

    def rekoble(self) -> bool:
        """Proev aa koble til paa nytt.

        Stoppar streaming, frigjer USB-handle, og koplar til på nytt.
        VIKTIG: Ingen dev.reset(), sysfs reset, eller EP2 recovery.
        Berre rein fråkopling/tilkopling. Viss EP2 ikkje fungerer
        etter rekoble, bruk "Gjenoppliv EP2"-knappen separat.
        """
        log.info("Rekoble: stoppar streaming og frigjer USB...")
        try:
            # 1. Stopp streaming FYRST (frigjer EP2 frå leser-traaden)
            if self._streamer:
                self.stopp_streaming()

            # 2. Frigjer USB-handle
            self.koble_fra()
            time.sleep(1.5)  # Gi USB-stakken tid til å frigjere handle

            # 3. Koble til att (berre find + configure + test EP2)
            self._koble_til_intern()
            return True
        except SiriusFeil as e:
            self._rekoble_forsok += 1
            log.error(f"Rekonnektering feilet ({self._rekoble_forsok}/{self._maks_rekoble}): {e}")
            return False

    # ---- Start Acquisition (fraa Wireshark pcapng-analyse) ----

    def _start_acquisition(self):
        """
        Send komplett start-acquisition-sekvens for aa starte EP2 ADC-streaming.

        Reverse-engineered fraa DewesoftX Wireshark USB-capture (sirius1.pcapng,
        2026-02-14). Konfigurerer ADC-sampling, DMA, kalibrering og triggar
        streaming-start via register 0x02.

        Sekvensen er 35 steg:
          1. A4 00 (pre-start modus)
          2. AC (hent slot-typar)
          3-34. Globale register-skrivingar via AD-kommando
          35. Register 0x02 trigger (startar EP2, ~137ms ventetid)

        Raises:
            SiriusPollTimeout: Viss trigger-registeret ikkje responderer
            SiriusUSBFeil: Ved USB-feil
        """
        proto = self._proto
        self._treng_heartbeat = True  # Etter start-acquisition treng SIRIUS heartbeat
        log.info("Start-acquisition sekvens (35 steg)...")

        # Steg 1: A4 00 (pre-start modus)
        log.info("  Steg 1/35: A4 00 (pre-start)")
        proto.send_prestart()

        # Steg 2: AC (hent slot-typar)
        log.info("  Steg 2/35: AC (slot-typar)")
        proto.hent_slot_typer()

        # Steg 3-34: Globale register-skrivingar
        # Format: AD 3F 0C 00 00 00 [reg] [8 bytes data]
        # Eksakte verdiar fraa DewesoftX pcapng (verifisert med tshark)
        regs = [
            # Sample rate og buffer
            (0x67, '80004e20005a0306'),   # Sample rate 20 kHz
            (0x7B, '00000c8000000040'),   # Buffer-konfig
        ]

        # ADC-konfig per kanal (reg 0x82, kanal 0-7)
        for ch in range(8):
            data = bytearray.fromhex('0000000000000031')
            data[3] = ch
            regs.append((0x82, data.hex()))

        # Timing, kontroll, filter, kalibrering
        regs.extend([
            (0xE5, '00001800ffffffff'),   # Timing/sync
            (0x6F, '3fff231fffffffff'),   # Kanal-enable-maske
            (0x72, '0000000200000000'),   # Trigger-konfig
            (0x10, '00000000ffffffff'),   # Kontroll
            (0x11, '00000000ffffffff'),   # Kontroll
            (0x07, '03000000ffffffff'),   # Mode/kontroll
            (0x9C, '00640064ffffffff'),   # Filter
            (0x98, '0214320000000000'),   # Desimering/averaging
            (0x99, '60600000ffffffff'),   # Sample timing
            (0x9D, '0000000000000000'),   # Tilleggskonfig
            (0x96, 'ffffffffffffffff'),   # Status-sjekk (les)
            (0xD0, '00000001ffffffff'),   # Stream enable
            (0x68, '000000ffffffffff'),   # DMA/transfer-konfig
            (0xCC, '000000c0ffffffff'),   # Tilleggskonfig
            (0xCD, '000001ffffffffff'),   # Tilleggskonfig
            (0xCA, '0010001000100010'),   # Kalibrering
            (0xCB, '0010001000100010'),   # Kalibrering
            (0xCE, '1010000000000000'),   # Tilleggskonfig
            (0xCF, '00000000ffffffff'),   # Tilleggskonfig
            (0x84, '0000000000000000'),   # Clear status
            (0xC8, 'ffffffffffffffff'),   # Status readback (les)
            (0x64, 'ffffffffffffffff'),   # Status readback (les)
        ])

        steg = 3
        for reg, data_hex in regs:
            cmd = bytes([0xAD, 0x3F, 0x0C, 0x00, 0x00, 0x00, reg]) + bytes.fromhex(data_hex)
            try:
                proto.send_ad_raa_og_poll(cmd)
                log.debug(f"  Steg {steg}/35: reg 0x{reg:02X} OK")
            except SiriusPollTimeout:
                log.warning(f"  Steg {steg}/35: reg 0x{reg:02X} poll timeout (held fram)")
            steg += 1

        # Steg 35: TRIGGER - Register 0x02 (startar EP2 ADC-streaming)
        # Denne tek ~137ms og krev mange B1-poll-syklusar.
        # er_skriving=False fordi vi MÅ vente på POLL_KLAR (0x01)
        log.info("  Steg 35/35: reg 0x02 TRIGGER (startar streaming)...")
        trigger_cmd = bytes.fromhex('ad3f0c00000002ffffffffffffffff')
        try:
            proto.send_ad_raa_og_poll(trigger_cmd, maks_forsok=1000,
                                      er_skriving=False)
            log.info("  Reg 0x02 trigger FULLFOERT (status=klar)")
        except SiriusPollTimeout:
            log.warning("  Reg 0x02 trigger: poll timeout (EP2 kan likevel starte)")

        log.info("Start-acquisition sekvens sendt")

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

        # Start ADC-leser-traad
        self._adc_traad = threading.Thread(
            target=self._adc_leser_loop,
            name="sirius-adc",
            daemon=True,
        )
        self._adc_traad.start()

        # Heartbeat (AE telemetri) berre etter start-acquisition.
        # I factory-default modus streamer EP2 av seg sjølv — heartbeat
        # på EP1 DREP EP2 ved å forstyrre SIRIUS sin idle-tilstand.
        # Etter start-acquisition treng SIRIUS heartbeat for å halde
        # EP2 gåande (DewesoftX sender AE kontinuerleg).
        if self._treng_heartbeat:
            self._heartbeat_traad = threading.Thread(
                target=self._heartbeat_loop,
                name="sirius-heartbeat",
                daemon=True,
            )
            self._heartbeat_traad.start()
            log.info("Streaming starta (ADC + heartbeat)")
        else:
            log.info("Streaming starta (ADC, heartbeat av — factory-default EP2)")

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

        Brukar 16384 bytes per lesing for aa matche SIRIUS sin faktiske
        pakkestorleik (15 872 bytes = 992 rammer x 8 kanalar x int16 LE).
        Med 512B-lesingar trengst 620+ lesingar/sek for aa halde tritt med
        20 kHz straumen (317 KB/s), noko som overvalder USB-stakken paa Pi.
        """
        io_feil_teller = 0

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

                if io_feil_teller >= 50:
                    log.error(
                        "For mange ADC I/O-feil (50) - stoppar streaming. "
                        "Bruk Gjenoppliv EP2 i web UI."
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

        EP2 format (fraa pcapng-analyse): 15 872 bytes per USB-pakke
        = 992 rammer x 8 kanalar x int16 LE. 20 pakkar/sek ved 20 kHz.
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
            "ep2_ok": self._ep2_ok,
        }

    def __repr__(self):
        return (
            f"SiriusDriver("
            f"tilkoblet={self._tilkoblet}, "
            f"streamer={self._streamer}, "
            f"eining='{self._enhetsinfo.enhetsstreng}', "
            f"sn='{self._enhetsinfo.serienummer}')"
        )
