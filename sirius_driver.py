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

import json
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

EP2_STRATEGI_STI = Path("/data/konfig/ep2_strategi.json")

# Alle kjende strategi-namn (i standard rekkjefylgje)
STRATEGI_NAMN = [
    "1_start_acquisition",
    "2_init_start",
    "3a_reset_åleine",
    "3b_reset_start",
    "4a_uhubctl_åleine",
    "4b_uhubctl_start",
]


class EP2StrategiLogg:
    """Registrerer kva EP2-strategi som lukkast og kor ofte.

    Lagrar til /data/konfig/ep2_strategi.json slik at data overlever restart.
    Formatet:
        {
            "historikk": [
                {"strategi": "3b_reset_start", "tid": "2026-02-15T12:34:56", "ok": true},
                ...
            ],
            "teljar": {"3b_reset_start": 5, "1_start_acquisition": 1, ...},
            "siste_suksess": "3b_reset_start",
            "feilet_totalt": 2
        }
    """

    def __init__(self, sti: Path = EP2_STRATEGI_STI):
        self._sti = sti
        self._data = self._last()

    def _last(self) -> dict:
        try:
            if self._sti.exists():
                return json.loads(self._sti.read_text(encoding="utf-8"))
        except Exception as e:
            log.debug(f"EP2 strategi-logg: kunne ikkje lese {self._sti}: {e}")
        return {"historikk": [], "teljar": {}, "siste_suksess": None, "feilet_totalt": 0}

    def _lagre(self):
        try:
            self._sti.parent.mkdir(parents=True, exist_ok=True)
            self._sti.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning(f"EP2 strategi-logg: kunne ikkje lagre: {e}")

    def registrer_suksess(self, strategi: str):
        """Registrer at ein strategi lukkast."""
        tid = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._data["historikk"].append(
            {"strategi": strategi, "tid": tid, "ok": True}
        )
        # Hald historikk rimeleg kort
        self._data["historikk"] = self._data["historikk"][-50:]
        self._data["teljar"][strategi] = self._data["teljar"].get(strategi, 0) + 1
        self._data["siste_suksess"] = strategi
        log.info(f"EP2 strategi-statistikk: '{strategi}' lukkast "
                 f"(totalt {self._data['teljar'][strategi]}x)")
        self._lagre()

    def registrer_feil(self):
        """Registrer at alle strategiar feilet."""
        tid = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._data["historikk"].append(
            {"strategi": "ALLE_FEILET", "tid": tid, "ok": False}
        )
        self._data["historikk"] = self._data["historikk"][-50:]
        self._data["feilet_totalt"] = self._data.get("feilet_totalt", 0) + 1
        self._lagre()

    @property
    def siste_suksess(self) -> Optional[str]:
        return self._data.get("siste_suksess")

    @property
    def teljar(self) -> dict:
        return dict(self._data.get("teljar", {}))

    @property
    def historikk(self) -> list:
        return list(self._data.get("historikk", []))

    def samandrag(self) -> dict:
        """Kompakt samandrag for API/web UI."""
        return {
            "siste_suksess": self._data.get("siste_suksess"),
            "teljar": self._data.get("teljar", {}),
            "feilet_totalt": self._data.get("feilet_totalt", 0),
            "siste_10": self._data.get("historikk", [])[-10:],
        }


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
        self._strategi_logg = EP2StrategiLogg()

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
            # VIKTIG: B1-heartbeat må pumpe medan vi testar EP2, elles
            # stoppar SIRIUS ADC-straumen umiddelbart etter trigger.
            log.info("EP2 ikkje aktiv — køyrer start-acquisition automatisk...")
            try:
                self._start_acquisition()
                if self._test_ep2_med_heartbeat():
                    self._strategi_logg.registrer_suksess("1_start_acquisition")
                    log.info("EP2 starta etter automatisk start-acquisition")
                else:
                    log.warning("EP2 svarte ikkje etter start-acquisition — prøver init + start...")
                    # Strategi 2: full init + start-acquisition
                    try:
                        self._initialiser()
                        time.sleep(0.5)
                        self._start_acquisition()
                        if self._test_ep2_med_heartbeat():
                            self._strategi_logg.registrer_suksess("2_init_start")
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
        """Frigjer USB-handle utan full koble_fra (unngår stopp_streaming).

        Tre-stegs frigjering (same som koble_fra) for å unngå EBUSY:
        1. release_interface(0)
        2. attach_kernel_driver(0) — gir interface tilbake til kernel
        3. dispose_resources()
        """
        if self._dev is not None:
            try:
                usb.util.release_interface(self._dev, 0)
            except Exception:
                pass
            try:
                self._dev.attach_kernel_driver(0)
            except (usb.core.USBError, NotImplementedError):
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
            # pyusb brukar ein global libusb-kontekst som kan ha stale cache
            # etter USB reset/power-cycle.  Prøv fersk kontekst.
            log.info("  Standard pyusb finn ikkje — prøver fersk libusb-kontekst...")
            try:
                from usb.backend import libusb1 as _libusb1
                fresh_be = _libusb1.get_backend()
                dev = usb.core.find(
                    idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID,
                    backend=fresh_be,
                )
            except Exception:
                pass
        if dev is None:
            # Siste utveg: etter uhubctl i Docker kan /dev/bus/usb/-noden
            # mangle sjølv om sysfs ser devicet.  Opprett noden frå sysfs-info.
            if SiriusDriver._sikre_dev_node():
                log.info("  /dev node sikra — prøver fersk libusb-kontekst på nytt...")
                try:
                    from usb.backend import libusb1 as _libusb1
                    fresh_be = _libusb1.get_backend()
                    dev = usb.core.find(
                        idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID,
                        backend=fresh_be,
                    )
                    if dev is None:
                        # Diagnostikk: list ALLE USB-einingar libusb kan sjå
                        alle = list(usb.core.find(
                            find_all=True, backend=fresh_be,
                        ) or [])
                        log.warning(
                            f"  libusb ser {len(alle)} einingar, "
                            f"ingen med VID=0x{DEWESOFT_VID:04X}: "
                            + ", ".join(
                                f"{d.idVendor:04x}:{d.idProduct:04x}"
                                for d in alle[:10]
                            )
                        )
                except Exception as e:
                    log.warning(f"  Fersk libusb-kontekst feilet: {e}")
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
            feil_str = str(e).lower()
            if "errno 19" in feil_str or "no such device" in feil_str:
                # Stale handle — devicet har ny adresse etter reset/power-cycle.
                # Forkast stale handle og finn eininga på nytt med fersk kontekst.
                log.warning("  set_configuration ENODEV — stale handle, prøver fersk kontekst...")
                try:
                    usb.util.dispose_resources(dev)
                except Exception:
                    pass
                try:
                    from usb.backend import libusb1 as _libusb1
                    fresh_be = _libusb1.get_backend()
                    dev = usb.core.find(
                        idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID,
                        backend=fresh_be,
                    )
                    if dev is None:
                        # Docker: /dev node kan mangle etter uhubctl
                        if SiriusDriver._sikre_dev_node():
                            fresh_be = _libusb1.get_backend()
                            dev = usb.core.find(
                                idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID,
                                backend=fresh_be,
                            )
                    if dev is None:
                        raise SiriusIkkeFunnet(
                            f"SIRIUS forsvann etter ENODEV "
                            f"(VID=0x{DEWESOFT_VID:04X}, PID=0x{SIRIUS_PID:04X})"
                        )
                    log.info(f"  Fann SIRIUS på nytt: Bus {dev.bus}, Adresse {dev.address}")
                    try:
                        if dev.is_kernel_driver_active(0):
                            dev.detach_kernel_driver(0)
                    except (usb.core.USBError, NotImplementedError):
                        pass
                    dev.set_configuration()
                    log.info("  set_configuration() OK (fersk kontekst)")
                except (SiriusIkkeFunnet, SiriusUSBFeil):
                    raise
                except Exception as e2:
                    raise SiriusUSBFeil(
                        f"set_configuration feilet etter fersk kontekst: {e2}"
                    ) from e2
            elif "resource busy" in feil_str or "errno 16" in feil_str:
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
                    if "Resource busy" not in str(e2) and "errno 16" not in str(e2).lower():
                        raise SiriusUSBFeil(f"set_configuration feilet: {e2}") from e2
                    # EBUSY vedvarer — ein annan prosess/tråd held interfacet.
                    # dev.reset() tvingar kernel til å frigjere ALLE interface-claims.
                    log.warning("  EBUSY vedvarer — tvingar frigjering med dev.reset()...")
                    try:
                        dev.reset()
                        time.sleep(2)
                        try:
                            usb.util.dispose_resources(dev)
                        except Exception:
                            pass
                        from usb.backend import libusb1 as _libusb1
                        fresh_be = _libusb1.get_backend()
                        dev = usb.core.find(
                            idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID,
                            backend=fresh_be,
                        )
                        if dev is None:
                            if SiriusDriver._sikre_dev_node():
                                fresh_be = _libusb1.get_backend()
                                dev = usb.core.find(
                                    idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID,
                                    backend=fresh_be,
                                )
                        if dev is None:
                            raise SiriusIkkeFunnet(
                                "SIRIUS forsvann etter EBUSY-reset"
                            )
                        try:
                            if dev.is_kernel_driver_active(0):
                                dev.detach_kernel_driver(0)
                        except (usb.core.USBError, NotImplementedError):
                            pass
                        dev.set_configuration()
                        log.info(f"  set_configuration() OK (etter dev.reset, "
                                 f"Bus {dev.bus} Adr {dev.address})")
                    except (SiriusIkkeFunnet, SiriusUSBFeil):
                        raise
                    except Exception as e3:
                        log.warning(f"  dev.reset EBUSY-recovery feilet: {e3} — held fram likevel")
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
    def _sikre_dev_node():
        """Sjekk at /dev/bus/usb/ device-noden eksisterer for SIRIUS.

        Etter uhubctl power-cycle inne i Docker kan /dev/bus/usb/-noden
        mangle fordi bind-mount ikkje alltid propagerer nye device-nodar
        fraa hosten.  Les busnum/devnum fraa sysfs og opprett noden med
        mknod viss den manglar.

        Returns: True viss noden vart oppretta eller allereie fanst
        """
        dev_dir = SiriusDriver._finn_sysfs_sti()
        if dev_dir is None:
            log.info("  _sikre_dev_node: SIRIUS ikkje i sysfs")
            return False

        try:
            busnum = int((dev_dir / "busnum").read_text().strip())
            devnum = int((dev_dir / "devnum").read_text().strip())
        except (OSError, ValueError) as e:
            log.debug(f"  Kan ikkje lese busnum/devnum fraa sysfs: {e}")
            return False

        log.info(f"  sysfs: {dev_dir.name} → bus={busnum} dev={devnum}")

        dev_path = Path(f"/dev/bus/usb/{busnum:03d}/{devnum:03d}")
        bus_dir = dev_path.parent

        # List kva som finst i /dev/bus/usb/BBB/ for diagnostikk
        if bus_dir.exists():
            try:
                nodar = sorted(bus_dir.iterdir())
                log.info(f"  /dev/bus/usb/{busnum:03d}/: "
                         f"{', '.join(n.name for n in nodar) or '(tom)'}")
            except Exception:
                pass

        if dev_path.exists():
            # Verifiser at vi kan opne fila (ikkje stale/ubrukeleg)
            import os
            try:
                fd = os.open(str(dev_path), os.O_RDWR)
                os.close(fd)
                log.info(f"  /dev node OK: {dev_path} (open+close OK)")
            except OSError as e:
                log.warning(f"  /dev node finst men kan ikkje opnast: {dev_path}: {e}")
            return True

        # Opprett katalog og device-node
        log.info(f"  /dev/bus/usb/{busnum:03d}/{devnum:03d} manglar — opprettar med mknod...")
        try:
            bus_dir.mkdir(parents=True, exist_ok=True)
            # USB device nodes: major=189, minor=(bus-1)*128+(dev-1)
            minor = (busnum - 1) * 128 + (devnum - 1)
            subprocess.run(
                ["mknod", str(dev_path), "c", "189", str(minor)],
                check=True, capture_output=True, timeout=5,
            )
            subprocess.run(
                ["chmod", "666", str(dev_path)],
                capture_output=True, timeout=5,
            )
            log.info(f"  Device-node oppretta: {dev_path} (major=189, minor={minor})")
            return True
        except Exception as e:
            log.warning(f"  Kan ikkje opprette /dev node: {e}")
            return False

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

    def _test_ep2_med_heartbeat(self, timeout=5000):
        """Test EP2 medan B1-heartbeat pumpar i bakgrunnen.

        Etter start-acquisition treng SIRIUS kontinuerlege B1-polls
        (~150/sek) for å halde EP2 aktiv.  Utan dette stoppar EP2
        innan millisekund etter at trigger-registeret er sett.

        Returns: True viss EP2 svarte med data
        """
        if self._proto is None:
            return self._test_ep2(timeout=timeout)

        hb_stopp = threading.Event()

        def _pumpe_b1():
            while not hb_stopp.is_set():
                try:
                    self._proto.poll_b1(timeout=10)
                except Exception:
                    hb_stopp.wait(timeout=0.005)

        hb_traad = threading.Thread(
            target=_pumpe_b1, daemon=True, name="ep2-test-hb"
        )
        hb_traad.start()
        try:
            return self._test_ep2(timeout=timeout)
        finally:
            hb_stopp.set()
            hb_traad.join(timeout=2)

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
            log.info(f"  uhubctl: port {port} power ON - ventar på enumerering...")
            time.sleep(2)  # Kort basisvent — kallaren handterer sysfs-polling

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

        # Frigjer eksisterande USB-handle for rein start.
        # Utan dette får strategi 1-2 EBUSY fordi det gamle handlet
        # framleis held interfacet klaimet etter stopp_streaming().
        self._frigjer_dev()
        try:
            self._koble_til_intern()
        except Exception as e:
            log.error(f"Kan ikkje koble til: {e}")
            return False

        # ============================================================
        # STRATEGI 1: Start-acquisition sekvens direkte
        # Sender dei 35 register-kommandoane som DewesoftX brukar
        # for aa starte EP2 ADC-streaming (reg 0x02 trigger).
        # ============================================================

        log.info("Strategi 1/4: Start-acquisition sekvens (direkte)...")
        try:
            self._start_acquisition()
            if self._test_ep2_med_heartbeat():
                self._strategi_logg.registrer_suksess("1_start_acquisition")
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
            if self._test_ep2_med_heartbeat():
                self._strategi_logg.registrer_suksess("2_init_start")
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
                    self._strategi_logg.registrer_suksess("3a_reset_åleine")
                    log.info("SUKSESS: Strategi 3a (dev.reset åleine)")
                    return True
                # EP2 ikkje oppe etter reset - proev start-sekvens
                try:
                    self._start_acquisition()
                    if self._test_ep2_med_heartbeat():
                        self._strategi_logg.registrer_suksess("3b_reset_start")
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
                # Etter power-cycle treng FX2 tid til firmware-lasting.
                # Vent til devicet dukkar opp i sysfs FØRST — dette unngår
                # at pyusb finn eit stale handle som gjev ENODEV og
                # øydelegg libusb sin interne device-liste.
                sysfs_ok = False
                for vent in range(8):
                    if self._finn_sysfs_sti() is not None:
                        log.info(f"  uhubctl: enhet synleg i sysfs (etter {vent}s)")
                        sysfs_ok = True
                        break
                    time.sleep(1)
                if not sysfs_ok:
                    log.warning("  uhubctl: enhet dukka ikkje opp i sysfs")

                # Ekstra ventetid etter sysfs — FX2 firmware treng tid
                # til å fullføre init før set_configuration() fungerer.
                time.sleep(3)

                for forsok in range(5):
                    try:
                        self._koble_til_intern()
                        break
                    except SiriusIkkeFunnet:
                        log.info(f"  uhubctl: pyusb finn ikkje enno (forsoek {forsok+1}/5)...")
                        time.sleep(3)
                    except Exception as e:
                        # ENODEV = FX2 ikkje klar enno — frigjer stale handle og vent
                        log.warning(f"  uhubctl tilkobling forsoek {forsok+1}: {e}")
                        self._frigjer_dev()
                        time.sleep(4)
                if self._ep2_ok:
                    self._strategi_logg.registrer_suksess("4a_uhubctl_åleine")
                    log.info("SUKSESS: Strategi 4a (uhubctl åleine)")
                    return True
                # EP2 ikkje oppe - proev start-sekvens
                if self._tilkoblet and self._proto:
                    try:
                        self._start_acquisition()
                        if self._test_ep2_med_heartbeat():
                            self._strategi_logg.registrer_suksess("4b_uhubctl_start")
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

        self._strategi_logg.registrer_feil()
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

        Fire-stegs frigjering for å unngå EBUSY ved neste tilkobling:
        0. Drep orphan-trådar via USB reset (KRITISK for å unngå EBUSY)
        1. release_interface(0) - frigjer pyusb auto-claim
        2. attach_kernel_driver(0) - gir interface tilbake til kernel
        3. dispose_resources() - frigjer backend-ressursar
        """
        if self._streamer:
            self.stopp_streaming()

        # Steg 0: Drep orphan-trådar som framleis blokkerer på USB I/O.
        # stopp_streaming() sett _stopp_event, men trådar kan blokkere på
        # dev.read() i opptil 1 sekund (timeout).  dev.reset() tvingar
        # ENODEV på ALLE blokkerande USB-operasjonar, slik at trådane
        # avsluttar umiddelbart.
        orphans = [
            t for t in threading.enumerate()
            if t.name in ("sirius-adc", "sirius-heartbeat") and t.is_alive()
        ]
        if orphans and self._dev is not None:
            log.warning(
                f"Orphan-trådar ({', '.join(t.name for t in orphans)}) "
                f"— tvingar avslutning med USB reset..."
            )
            try:
                self._dev.reset()
                log.info("USB reset sendt — ventar på at trådar avsluttar...")
            except Exception as e:
                log.debug(f"USB reset feilet (ikkje kritisk): {e}")
            for t in orphans:
                t.join(timeout=3)
            framleis = [t for t in orphans if t.is_alive()]
            if framleis:
                log.error(
                    f"{len(framleis)} orphan-tråd(ar) lever framleis etter USB reset!"
                )
            else:
                log.info("Alle orphan-trådar avslutta etter USB reset")

        self._tilkoblet = False

        if self._dev is not None:
            # Steg 1: Release interface 0 (auto-klaimet av pyusb ved I/O)
            # (kan feile etter USB reset — det er OK)
            try:
                usb.util.release_interface(self._dev, 0)
                log.debug("  release_interface(0) OK")
            except Exception:
                pass

            # Steg 2: Re-attach kernel driver for å tvinge full frigjering
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
        koble_fra() brukar dev.reset() for å tvinge orphan-trådar til å
        avslutte (ENODEV), noko som ogsaa re-enumererer USB-eininga.
        """
        log.info("Rekoble: stoppar streaming og frigjer USB...")
        try:
            # 1. Stopp streaming FYRST (frigjer EP2 frå leser-traaden)
            if self._streamer:
                self.stopp_streaming()

            # 2. Frigjer USB-handle (dev.reset() drep orphan-trådar)
            self.koble_fra()
            time.sleep(2.0)  # Gi USB tid til re-enumerering etter reset

            # 3. Koble til att (inkl. auto start-acquisition viss EP2 feilar)
            self.koble_til()
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

        # Steg 36: Register 0x03 — "streaming confirmed" signal
        # DewesoftX sender dette 0.22s etter reg 0x02, etter at EP2-data
        # allereie har begynt å strøyme. Handshake som fortel SIRIUS at
        # hosten mottek data og er klar.
        time.sleep(0.2)
        log.info("  Steg 36: reg 0x03 (streaming confirmed)...")
        confirm_cmd = bytes.fromhex('ad3f0c00000003ffffffffffffffff')
        try:
            proto.send_ad_raa_og_poll(confirm_cmd, maks_forsok=100,
                                      er_skriving=True)
            log.info("  Reg 0x03 confirm FULLFOERT")
        except SiriusPollTimeout:
            log.warning("  Reg 0x03 confirm: poll timeout (held fram)")

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

        # B1-heartbeat køyrer ALLTID under streaming.
        # pcapng-analyse viser at DewesoftX sender 0xB1 kontinuerleg i alle modi.
        # Tidlegare slo vi av heartbeat i factory-default fordi 0xAE drap EP2,
        # men 0xB1 (enkel poll) er trygt og naudsynt for å halde EP2 aktiv.
        self._heartbeat_traad = threading.Thread(
            target=self._heartbeat_loop,
            name="sirius-heartbeat",
            daemon=True,
        )
        self._heartbeat_traad.start()
        log.info("Streaming starta (ADC + B1-heartbeat)")

    def stopp_streaming(self):
        """Stopp streaming og vent paa at traader avslutter.

        ADC-tråden har maks 1s USB-timeout, så den sjekkar _stopp_event
        minst kvart sekund.  Viss trådar framleis lever etter join,
        vil koble_fra() tvinge dei ut med dev.reset().
        """
        if not self._streamer:
            return

        log.info("Stoppar streaming...")
        self._stopp_event.set()
        self._streamer = False

        current = threading.current_thread()
        if self._adc_traad and self._adc_traad.is_alive() and self._adc_traad is not current:
            self._adc_traad.join(timeout=3)
            if self._adc_traad.is_alive():
                log.warning("ADC-tråd lever framleis etter join(3s) — "
                            "koble_fra() vil tvinge avslutning")
        if self._heartbeat_traad and self._heartbeat_traad.is_alive() and self._heartbeat_traad is not current:
            self._heartbeat_traad.join(timeout=3)

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
        log.info("ADC-tråd starta")
        io_feil_teller = 0
        timeout_teller = 0
        pkt_teller = 0

        while not self._stopp_event.is_set():
            try:
                raa = self._proto.les_adc_data(
                    storrelse=16384,
                    timeout=1000
                )

                if raa:
                    pkt_teller += 1
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

                    if timeout_teller > 0:
                        log.info(f"ADC: data att etter {timeout_teller} timeouts "
                                 f"({len(raa)}B, pkt #{pkt_teller})")
                    timeout_teller = 0

                io_feil_teller = 0

            except SiriusUSBFeil as e:
                if self._stopp_event.is_set():
                    break

                feil_str = str(e).lower()
                if "timeout" in feil_str or "timed out" in feil_str:
                    timeout_teller += 1
                    # Logg fyrste timeout og deretter kvar 10.
                    if timeout_teller == 1:
                        log.info(f"ADC: EP2 timeout (pkt #{pkt_teller} hittil)")
                    elif timeout_teller % 10 == 0:
                        log.warning(f"ADC: {timeout_teller} EP2 timeouts på rad "
                                    f"— heartbeat-tråd lever: "
                                    f"{self._heartbeat_traad.is_alive() if self._heartbeat_traad else 'N/A'}")
                    self._stopp_event.wait(timeout=0.5)
                    continue

                # ENODEV (Errno 19) = eininga er fysisk borte etter reset/power-cycle.
                # Ingen vits å prøve på nytt — stopp umiddelbart så USB vert frigjeven.
                if "errno 19" in feil_str or "no such device" in feil_str:
                    log.error(f"ADC: Eining borte (ENODEV) — stoppar umiddelbart: {e}")
                    self._streamer = False
                    break

                # Ekte I/O-feil (Errno 5, Errno 16 etc.)
                io_feil_teller += 1
                log.warning(f"ADC I/O-feil ({io_feil_teller}): {e}")

                # Ved EBUSY: proev clear_halt for aa nullstille endepunktet
                if "errno 16" in feil_str or "resource busy" in feil_str:
                    try:
                        self._dev.clear_halt(EP_ADC_IN)
                        log.info("EP2 clear_halt etter EBUSY")
                    except Exception:
                        pass

                if io_feil_teller >= 10:
                    log.error(
                        "For mange ADC I/O-feil (10) - stoppar streaming. "
                        "Bruk Rekoble i web UI."
                    )
                    self._streamer = False
                    break

                # Kort pause foer retry
                self._stopp_event.wait(timeout=0.5)

            except Exception as e:
                if self._stopp_event.is_set():
                    break
                log.error(f"Uventa feil i ADC-loop: {e}")
                self._stopp_event.wait(timeout=1.0)

        log.info(f"ADC-tråd avslutta (pakkar={pkt_teller}, "
                 f"io_feil={io_feil_teller}, timeouts={timeout_teller})")

    def _heartbeat_loop(self):
        """Bakgrunnstraad: send B1 poll (heartbeat) kontinuerleg + AE telemetri.

        pcapng-analyse av DewesoftX viser to parallelle EP1-operasjonar:
          - 0xB1 poll: ~120-200/sek (held EP2 ADC-straumen aktiv)
          - 0xAE 1F 0C telemetri: ~9.5 Hz (instrument-helse/temperatur)

        Tidlegare brukte vi KUN 0xAE kvart 2. sek — feil opcode, feil rate.
        """
        feil_teller = 0
        poll_teller = 0
        ae_intervall = 10  # Send AE kvar 10. B1-poll (~10 Hz ved ~100 polls/sek)
        log.info("Heartbeat-tråd starta")
        while not self._stopp_event.is_set():
            try:
                # Hovud-heartbeat: B1 poll med kort timeout
                self._proto.poll_b1(timeout=10)
                feil_teller = 0
                poll_teller += 1

                # Periodisk AE telemetri (~10 Hz, som DewesoftX)
                if poll_teller % ae_intervall == 0:
                    try:
                        self._proto.send_telemetri()
                    except SiriusUSBFeil:
                        pass  # AE-feil er ikkje kritisk

                # Logg rate kvart 5. sekund (~500 polls)
                if poll_teller % 500 == 0:
                    log.info(f"Heartbeat: {poll_teller} B1-polls sendt (~{poll_teller//5}s)")

            except SiriusUSBFeil as e:
                feil_str = str(e).lower()
                if "errno 19" in feil_str or "no such device" in feil_str:
                    log.error(f"Heartbeat: Eining borte (ENODEV) — stoppar: {e}")
                    break
                feil_teller += 1
                if feil_teller >= 5:
                    log.error(f"Heartbeat: {feil_teller} feil på rad — stoppar: {e}")
                    break
                log.warning(f"Heartbeat B1-feil ({feil_teller}): {e}")
                self._stopp_event.wait(timeout=0.1)
        log.info(f"Heartbeat-tråd avslutta (polls={poll_teller}, feil={feil_teller})")

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
            "ep2_strategi": self._strategi_logg.samandrag(),
        }

    def __repr__(self):
        return (
            f"SiriusDriver("
            f"tilkoblet={self._tilkoblet}, "
            f"streamer={self._streamer}, "
            f"eining='{self._enhetsinfo.enhetsstreng}', "
            f"sn='{self._enhetsinfo.serienummer}')"
        )
