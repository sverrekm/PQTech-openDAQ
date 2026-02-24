#!/usr/bin/env python3
"""
SIRIUS Hoeynivaa-driver (Lag 2)
=================================
Brukervenlig driver med tilkobling, initialisering, konfigurasjon og streaming.

Initialiserings-sekvensen er eksakt replay av DewesoftX sin USB-trafikk,
reverse-engineered fraa sirius2.pcapng (USBPcap-fangst).

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
from collections import deque
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
    ADC_KANALER,
    SiriusFeil, SiriusUSBFeil, SiriusPollTimeout, SiriusIkkeFunnet,
    TIMEOUT_DATA,
    REG_EXC_ENABLE, REG_EXC_VOLT, REG_EXC_OFF,
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
    sample_rate: int = 20000
    aktive_kanaler: list = field(default_factory=lambda: list(range(ADC_KANALER)))
    varighet_sek: float = 5.0


class SiriusDriver:
    """
    Hoeynivaa-driver for Dewesoft SIRIUS.

    Haandterer tilkobling, initialisering, konfigurasjon og streaming.
    Initialiserings-sekvensen repliserer DewesoftX sin protokoll.
    """

    # Klasse-nivå event for å drepe orphan-trådar frå tidlegare driver-instansar.
    # Når rekoble_driver() finn orphan sirius-trådar utan stopp-event-referanse,
    # set den dette eventet.  Alle heartbeat/ADC-løkker sjekkar det i tillegg
    # til instansens eigen _stopp_event.
    _orphan_kill = threading.Event()

    def __init__(self):
        self._proto: Optional[SiriusProtokoll] = None
        self._dev = None
        self._tilkoblet = False
        self._streamer = False
        self._stopp_event = threading.Event()
        self._adc_traad: Optional[threading.Thread] = None
        self._heartbeat_traad: Optional[threading.Thread] = None
        self._data_callback: Optional[Callable] = None
        self._data_buffer = deque(maxlen=1000)
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
        # Midlertidig B1-pumpe som held EP2 i live mellom koble_til() og
        # start_streaming().  _heartbeat_loop() stoppar den når den tek over.
        self._temp_hb_stopp: Optional[threading.Event] = None
        self._temp_hb_traad: Optional[threading.Thread] = None

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
            # med B1-heartbeat pumpande FRÅ STARTEN.
            #
            # KRITISK: B1-pumpa startar FØR _start_acquisition() slik at
            # det IKKJE er nokon heartbeat-gap mellom trigger-poll-KLAR
            # og fyrste eksterne B1.  Pumpa er allereie parkert på
            # _cmd_lock og tek over med ein gong trigger returnerer.
            # I pcapng sender DewesoftX B1 kontinuerleg utan gap.
            log.info("EP2 ikkje aktiv — køyrer start-acquisition med B1-pumpe...")
            hb_stopp = threading.Event()
            hb_teller = [0]

            def _b1_pumpe_koble():
                while not hb_stopp.is_set():
                    try:
                        self._proto.poll_b1(timeout=10)
                        hb_teller[0] += 1
                    except Exception:
                        hb_stopp.wait(timeout=0.005)
                    # Drain EP4/EP6 — SIRIUS krev at ALLE data-endepunkt
                    # vert lesne samstundes, elles fylst FIFO og EP2 stoppar.
                    # EP6 er 15872B/pkt (same som EP2), EP4 er 20B/pkt.
                    for ep, sz in [(EP_CTRL_IN, 64), (EP_SYNC_IN, 16384)]:
                        try:
                            self._dev.read(ep, sz, timeout=1)
                        except Exception:
                            pass

            hb_traad = threading.Thread(
                target=_b1_pumpe_koble, daemon=True, name="koble-til-hb"
            )
            hb_traad.start()

            behald_pumpe = False
            try:
                try:
                    self._start_acquisition()
                    log.info(f"  B1-pumpe: {hb_teller[0]} polls under start-acquisition")
                    if self._test_ep2():
                        self._strategi_logg.registrer_suksess("1_start_acquisition")
                        log.info("EP2 starta etter automatisk start-acquisition")
                        behald_pumpe = True
                    else:
                        log.warning("EP2 svarte ikkje — prøver init + start...")
                        # Strategi 2: init + start-acquisition
                        # INGA time.sleep() — B1-pumpa held hjarta bankande
                        try:
                            self._initialiser()
                            self._start_acquisition()
                            log.info(f"  B1-pumpe: {hb_teller[0]} polls totalt")
                            if self._test_ep2():
                                self._strategi_logg.registrer_suksess("2_init_start")
                                log.info("EP2 starta etter init + start-acquisition")
                                behald_pumpe = True
                        except Exception as e2:
                            log.warning(f"Init + start-acquisition feilet: {e2}")
                except Exception as e:
                    log.warning(f"Automatisk start-acquisition feilet: {e}")
            finally:
                if behald_pumpe:
                    # IKKJE stopp pumpa!  EP2 treng kontinuerleg B1 heartbeat.
                    # _heartbeat_loop() tek over og stoppar temp-pumpa.
                    self._temp_hb_stopp = hb_stopp
                    self._temp_hb_traad = hb_traad
                    log.info("  B1-pumpe held i live til heartbeat-tråd tek over")
                else:
                    hb_stopp.set()
                    hb_traad.join(timeout=2)

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
        # Stopp temp B1-pumpe fyrst
        if self._temp_hb_stopp is not None:
            self._temp_hb_stopp.set()
            self._temp_hb_stopp = None
            self._temp_hb_traad = None

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

        # 3. Test EP2 direkte (3s timeout — forventa å feile på fyrste boot)
        log.info("Testar EP2 (ADC-data)...")
        self._ep2_ok = False
        try:
            test_ep2 = dev.read(EP_ADC_IN, 16384, timeout=3000)
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

    def _test_ep2(self, timeout=5000):
        """Test om EP2 leverer data (fleire forsøk med stor buffer).

        Brukar 16384-byte buffer (matchande EP2 pakkestorleik 15872B)
        og prøver fleire korte lesingar opp til total timeout.
        Kallar clear_halt fyrst i tilfelle EP2 er stalla.

        Returns: True viss EP2 svarte med data
        """
        # Fjern eventuell stall/halt-tilstand på EP2, EP4 og EP6.
        # DewesoftX les ALLE tre endepunkt samstundes — viss EP4/EP6
        # ikkje vert drenert, fylst FIFO-ane og blokkerer EP2.
        for ep in [EP_ADC_IN, EP_CTRL_IN, EP_SYNC_IN]:
            try:
                self._dev.clear_halt(ep)
            except Exception:
                pass

        start = time.time()
        timeout_sek = timeout / 1000.0
        forsok = 0
        while time.time() - start < timeout_sek:
            forsok += 1
            try:
                data = self._dev.read(EP_ADC_IN, 16384, timeout=500)
                if data and len(data) > 0:
                    log.info(f"  EP2 test OK: {len(data)} bytes "
                             f"(forsøk #{forsok}, {time.time()-start:.1f}s)")
                    self._ep2_ok = True
                    return True
            except usb.core.USBError:
                pass  # timeout — prøv att
        log.warning(f"  EP2 test: ingen data etter {forsok} forsøk "
                    f"({timeout_sek:.0f}s)")
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

        # B1-pumpe for strategiar 1-2 (same USB-handle).
        # Startar FØR trigger slik at pumpa allereie ventar på _cmd_lock
        # og tek over med ein gong send_ad_raa_og_poll returnerer.
        hb_stopp = threading.Event()
        hb_teller = [0]

        def _b1_pumpe_gjenoppliv():
            while not hb_stopp.is_set():
                try:
                    self._proto.poll_b1(timeout=10)
                    hb_teller[0] += 1
                except Exception:
                    hb_stopp.wait(timeout=0.005)
                # Drain EP4/EP6 — SIRIUS krev at ALLE data-endepunkt
                # vert lesne samstundes, elles fylst FIFO og EP2 stoppar.
                for ep in [EP_CTRL_IN, EP_SYNC_IN]:
                    try:
                        self._dev.read(ep, 512, timeout=1)
                    except Exception:
                        pass

        hb_traad = threading.Thread(
            target=_b1_pumpe_gjenoppliv, daemon=True, name="gjenoppliv-hb"
        )
        hb_traad.start()

        behald_pumpe = False
        try:
            # ============================================================
            # STRATEGI 1: Start-acquisition sekvens direkte
            # Sender dei 35 register-kommandoane som DewesoftX brukar
            # for aa starte EP2 ADC-streaming (reg 0x02 trigger).
            # B1-pumpa køyrer parallelt og held heartbeat utan gap.
            # ============================================================

            log.info("Strategi 1/4: Start-acquisition sekvens (direkte)...")
            try:
                self._start_acquisition()
                log.info(f"  B1-pumpe: {hb_teller[0]} polls under start-acquisition")
                if self._test_ep2():
                    self._strategi_logg.registrer_suksess("1_start_acquisition")
                    log.info("SUKSESS: Strategi 1 (start-acquisition)")
                    behald_pumpe = True
                    return True
                log.info("  Start-acquisition sendt, men EP2 svarte ikkje enno")
            except (SiriusPollTimeout, SiriusUSBFeil) as e:
                log.warning(f"  Strategi 1 feilet: {e}")

            # ============================================================
            # STRATEGI 2: Init-sekvens + start-acquisition
            # Kjoer full DewesoftX-syklus: init fyrst (A0/A1/A8/B0/AD),
            # deretter start-acquisition. INGA pause — B1-pumpa held
            # hjarta bankande mellom init og start.
            # ============================================================
            log.info("Strategi 2/4: Init + start-acquisition (full DewesoftX-syklus)...")
            try:
                self._initialiser()
                # INGA time.sleep() — B1-pumpa held heartbeat gåande
                self._start_acquisition()
                log.info(f"  B1-pumpe: {hb_teller[0]} polls totalt")
                if self._test_ep2():
                    self._strategi_logg.registrer_suksess("2_init_start")
                    log.info("SUKSESS: Strategi 2 (init + start-acquisition)")
                    behald_pumpe = True
                    return True
                log.info("  Init + start sendt, men EP2 svarte ikkje")
            except (SiriusPollTimeout, SiriusUSBFeil) as e:
                log.warning(f"  Strategi 2 feilet: {e}")
        finally:
            if behald_pumpe:
                self._temp_hb_stopp = hb_stopp
                self._temp_hb_traad = hb_traad
                log.info("  B1-pumpe held i live til heartbeat-tråd tek over")
            else:
                hb_stopp.set()
                hb_traad.join(timeout=2)

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
                # EP2 ikkje oppe etter reset - proev full init + start
                try:
                    self._initialiser()
                    self._start_acquisition()
                    if self._test_ep2_med_heartbeat():
                        self._strategi_logg.registrer_suksess("3b_reset_start")
                        log.info("SUKSESS: Strategi 3b (dev.reset + init + start)")
                        return True
                except (SiriusPollTimeout, SiriusUSBFeil) as e:
                    log.warning(f"  Init + start etter reset feilet: {e}")
        except Exception as e:
            log.warning(f"  Strategi 3 feilet: {e}")
            try:
                self._koble_til_intern()
            except Exception:
                pass

        # ============================================================
        # STRATEGI 4: uhubctl power-cycle + init + start-acquisition
        # Fysisk USB-straumkutt, koble til att, kjoer full init.
        # ============================================================
        log.info("Strategi 4/4: uhubctl + init + start-acquisition...")
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
                # EP2 ikkje oppe - proev full init + start
                if self._tilkoblet and self._proto:
                    try:
                        self._initialiser()
                        self._start_acquisition()
                        if self._test_ep2_med_heartbeat():
                            self._strategi_logg.registrer_suksess("4b_uhubctl_start")
                            log.info("SUKSESS: Strategi 4b (uhubctl + init + start)")
                            return True
                    except (SiriusPollTimeout, SiriusUSBFeil) as e:
                        log.warning(f"  Init + start etter uhubctl feilet: {e}")
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
        # Stopp temp B1-pumpe viss den framleis køyrer
        if self._temp_hb_stopp is not None:
            self._temp_hb_stopp.set()
            self._temp_hb_stopp = None
            self._temp_hb_traad = None

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

    # ---- Excitation voltage (integrator-spenning for Lo-LV slotter) ----

    def _sett_excitation(self):
        """
        Sett excitation-spenning for Lo-LV slotter (slot 4-6).

        Rogowski-spoler og andre integrator-baserte sensorar treng 5V unipolar
        excitation fraa SIRIUS-forsterkaren. DewesoftX set dette automatisk
        ved kanaloppsett — vi repliserer kommandosekvensen fraa pcapng-analyse.

        Kommandoar (per slot):
          ON:  A5 02 C3 01 (enable) → commit → A5 02 BC 04 (5V) → commit
          OFF: A5 02 C9 00 (disable) → commit
        """
        proto = self._proto

        # Les kanal-konfig for å finne kva slotter som treng excitation
        try:
            from kanal_konfig import les_konfig
            konfig = les_konfig()
        except Exception as e:
            log.warning(f"Kunne ikkje lese kanal-konfig for excitation: {e}")
            return

        for kk in konfig:
            slot = kk.indeks
            if slot > 7:
                continue  # Berre fysiske ADC-slotter

            if kk.sensor_aktiv:
                # Enable 5V unipolar excitation
                try:
                    proto.a5_set_mode(slot, REG_EXC_ENABLE, 0x01)
                    proto.commit(slot)
                    proto.a5_set_mode(slot, REG_EXC_VOLT, 0x04)  # 0x04 = 5V
                    proto.commit(slot)
                    log.info(f"  Slot {slot} ({kk.namn}): excitation 5V ON")
                except (SiriusPollTimeout, SiriusUSBFeil) as e:
                    log.warning(f"  Slot {slot} excitation feil: {e}")

        log.info("Excitation-oppsett fullfoert")

    # ---- Start Acquisition (fraa Wireshark pcapng-analyse) ----

    def _start_acquisition(self):
        """
        Send komplett start-acquisition-sekvens for aa starte EP2 ADC-streaming.

        Reverse-engineered fraa DewesoftX Wireshark USB-capture (sirius2.pcapng).
        Konfigurerer ADC-sampling, DMA, kalibrering og triggar
        streaming-start via register 0x02.

        Sekvensen:
          1. A4 00 (pre-start modus)
          2. AC (hent slot-typar)
          3-34. Globale register-skrivingar via AD-kommando
          35. Register 0x02 trigger (startar EP2, ~137ms ventetid)

        Raises:
            SiriusPollTimeout: Viss trigger-registeret ikkje responderer
            SiriusUSBFeil: Ved USB-feil
        """
        proto = self._proto
        self._treng_heartbeat = True
        log.info("Start-acquisition sekvens...")

        # Steg 1: A4 00 (pre-start modus)
        log.info("  A4 00 (pre-start)")
        proto.send_prestart()

        # Steg 2: AC (hent slot-typar)
        log.info("  AC (slot-typar)")
        proto.hent_slot_typer()

        # Steg 2b: Sett excitation-spenning for Lo-LV slotter
        self._sett_excitation()

        # Steg 3-34: Globale register-skrivingar
        # Eksakte verdiar fraa DewesoftX sirius2.pcapng
        streaming_regs = [
            'ad3f0c0000006780004e20005a0306',   # Sample rate 20 kHz
            'ad3f0c0000007b00000c8000000040',   # Buffer-konfig
            'ad3f0c000000820000000000000031',   # ADC ch 0
            'ad3f0c000000820000000100000031',   # ADC ch 1
            'ad3f0c000000820000000200000031',   # ADC ch 2
            'ad3f0c000000820000000300000031',   # ADC ch 3
            'ad3f0c000000820000000400000031',   # ADC ch 4
            'ad3f0c000000820000000500000031',   # ADC ch 5
            'ad3f0c000000820000000600000031',   # ADC ch 6
            'ad3f0c000000820000000700000031',   # ADC ch 7
            'ad3f0c000000e500001800ffffffff',   # Timing/sync
            'ad3f0c0000006f3fff231fffffffff',   # Kanal-enable-maske
            'ad3f0c000000720000000200000000',   # Trigger-konfig
            'ad3f0c0000001000000000ffffffff',   # Kontroll
            'ad3f0c0000001100000000ffffffff',   # Kontroll
            'ad3f0c0000000703000000ffffffff',   # Mode/kontroll
            'ad3f0c0000009c00640064ffffffff',   # Filter
            'ad3f0c000000980214320000000000',   # Desimering/averaging
            'ad3f0c0000009960600000ffffffff',   # Sample timing
            'ad3f0c0000009d0000000000000000',   # Tilleggskonfig
            'ad3f0c00000096ffffffffffffffff',   # ADC trigger
            'ad3f0c000000d000000001ffffffff',   # DMA enable
            'ad3f0c00000068000000ffffffffff',   # Buffer reset
            'ad3f0c000000cc000000c0ffffffff',   # DMA config
            'ad3f0c000000cd000001ffffffffff',   # DMA mode
            'ad3f0c000000ca0010001000100010',   # Kanal-maske slots 0-3
            'ad3f0c000000cb0010001000100010',   # Kanal-maske slots 4-7
            'ad3f0c000000ce1010000000000000',   # Slot enable-maske
            'ad3f0c000000cf00000000ffffffff',   # Clear flags
            'ad3f0c000000840000000000000000',   # Transfer start
            'ad3f0c000000c8ffffffffffffffff',   # DMA arm
            'ad3f0c00000064ffffffffffffffff',   # Streaming start
        ]

        for i, cmd_hex in enumerate(streaming_regs):
            cmd = bytes.fromhex(cmd_hex)
            reg = cmd[6]
            # Reg 0x96 (apply config) treng ~284 B1-polls
            maks = 400 if reg == 0x96 else 50
            try:
                proto.send_ad_raa_og_poll(cmd, maks_forsok=maks)
                log.debug(f"  Reg 0x{reg:02X} OK")
            except SiriusPollTimeout:
                log.warning(f"  Reg 0x{reg:02X} poll timeout (held fram)")

        # TRIGGER - Register 0x02 (startar EP2 ADC-streaming)
        # er_skriving=False fordi vi MÅ vente på POLL_KLAR (0x01)
        log.info("  Reg 0x02 TRIGGER (startar streaming)...")
        trigger_cmd = bytes.fromhex('ad3f0c00000002ffffffffffffffff')
        try:
            proto.send_ad_raa_og_poll(trigger_cmd, maks_forsok=1000,
                                      er_skriving=False)
            log.info("  Reg 0x02 trigger FULLFOERT (status=klar)")
        except SiriusPollTimeout:
            log.warning("  Reg 0x02 trigger: poll timeout (EP2 kan likevel starte)")

        log.info("Start-acquisition sekvens sendt")

    # ---- Initialisering (repliserer DewesoftX-sekvensen fraa pcapng) ----

    def _initialiser(self):
        """
        Kjoer full init-sekvens basert paa eksakt replay av DewesoftX USB-trafikk.

        Replay av alle kommandoar fraa sirius2.pcapng FOER streaming-start (A4).
        Sekvensen inkluderer:

        Fase 1: Enhetsoppdaging (A1 slot-kart, A0 aktiv modus, 00 FW-versjon)
        Fase 2: EEPROM-lesing (A8 x64 - kalibrering, serienummer, etc.)
        Fase 3: Init/config-modus (B0 3F 0C)
        Fase 4: Globale AD register-operasjonar (0x06, 0x08, 0x00, 0x01,
                 0x17, 0x15 slot-presence config)
        Fase 5: Per-slot initialisering via AD 3F 0C (A5 command dispatch,
                 EEPROM-kalibrering, modulidentifikasjon)
        Fase 6: Flush dataendepunkt

        Totalt ~1000+ AD-kommandoar med B1-poll per stykke.
        """
        from sirius_init_sekvens import INIT_SEKVENS

        log.info("Initialiserer SIRIUS (pcapng replay)...")
        proto = self._proto

        ad_teller = 0
        enkel_teller = 0
        feil_teller = 0
        fase = "init"
        eeprom_data = bytearray()  # Samla EEPROM-data for serienummer-søk

        for cmd_hex in INIT_SEKVENS:
            cmd = bytes.fromhex(cmd_hex)
            opcode = cmd[0]

            # Stopp foer A4 (pre-start) — streaming-start vert handtert av
            # _start_acquisition() separat.
            if opcode == 0xA4:
                break

            if opcode == 0xAD:
                # AD-kommando med B1-polling
                reg = cmd[6]

                # Avgjer om dette er skriving eller lesing for B1-poll-logikk:
                #   0x13 = slot-skriving → aksepter status=0x00 med ein gong
                #   0x14, 0x08, 0x0C, 0x1C = lesing → vent på status=0x01
                #   Andre (globale reg) = skriving viss data != all-FF
                if reg == 0x13:
                    er_skriving = True
                elif reg in (0x14, 0x08, 0x0C, 0x1C):
                    er_skriving = False
                else:
                    er_skriving = not all(b == 0xFF for b in cmd[7:15])

                # Visse register treng mange B1-polls (fraa pcapng-analyse):
                #   0x0B (sync/kalibrering): ~122 polls
                #   0x96 (apply config): ~284 polls
                #   0x02 (start acquisition): ~370 polls
                lang_poll = {0x0B: 200, 0x96: 400, 0x02: 500}
                maks = lang_poll.get(reg, 50)

                try:
                    svar = proto.send_ad_raa_og_poll(
                        cmd[:15],
                        er_skriving=er_skriving,
                        maks_forsok=maks,
                    )
                    ad_teller += 1
                except SiriusPollTimeout:
                    feil_teller += 1
                    if feil_teller <= 5:
                        log.debug(f"  AD reg=0x{reg:02X} poll timeout (held fram)")
                except SiriusUSBFeil as e:
                    feil_teller += 1
                    log.warning(f"  AD reg=0x{reg:02X} USB-feil: {e}")

                # Logg framdrift
                if ad_teller > 0 and ad_teller % 200 == 0:
                    log.info(f"  Init: {ad_teller} AD-kommandoar sendt "
                             f"({feil_teller} feil)...")

            else:
                # Enkel kommando (A1, A0, 00, A8, B0, AC)
                try:
                    svar = proto.send_raa_kommando(cmd)
                    enkel_teller += 1

                    # Fang opp metadata frå svar
                    if opcode == 0xA1:
                        self._enhetsinfo.slot_tilstedevaerelse = svar
                        log.info(f"  Slot-kart: {svar[:16].hex()}")
                    elif opcode == 0x00:
                        self._enhetsinfo.fw_versjon = svar
                        log.info(f"  FW-versjon: {svar[:8].hex()}")
                    elif opcode == 0xAC:
                        self._enhetsinfo.slot_typer = svar
                        log.info(f"  Slot-typar: {svar[:16].hex()}")
                    elif opcode == 0xB0:
                        log.info("  B0 3F 0C init/config-modus sett")
                        fase = "global_ad"
                    elif opcode == 0xA0:
                        log.info("  A0 01 aktiv modus sett")
                    elif opcode == 0xA8:
                        # Samla EEPROM-data for serienummer-søk
                        if svar and len(svar) > 0:
                            eeprom_data.extend(svar)
                        if enkel_teller <= 2:
                            log.info(f"  EEPROM-lesing startar ({cmd[1:].hex()})...")

                except SiriusUSBFeil as e:
                    feil_teller += 1
                    log.warning(f"  Cmd 0x{opcode:02X} feilet: {e}")

        # Søk etter Dewesoft-serienummer i EEPROM-data.
        # Ekte serienummer er ASCII-streng som "DB19106004" (2 bokstavar + 8 siffer).
        # USB iSerialNumber (t.d. D019274CF6) er FX2-kontrollaren — ikkje Dewesoft.
        if eeprom_data:
            import re
            log.info(f"  EEPROM: {len(eeprom_data)} bytes samla")
            # Logg fyrste 64 bytes for feilsøking (hex + ASCII)
            for offset in range(0, min(len(eeprom_data), 128), 16):
                chunk = eeprom_data[offset:offset + 16]
                hex_part = ' '.join(f'{b:02x}' for b in chunk)
                ascii_part = ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in chunk)
                log.info(f"  EEPROM[{offset:04x}]: {hex_part:<48s} {ascii_part}")

            # Søk etter Dewesoft-serienummer: 2 store bokstavar + 8 siffer
            eeprom_ascii = eeprom_data.decode('ascii', errors='replace')
            match = re.search(r'[A-Z]{2}\d{8}', eeprom_ascii)
            if match:
                dewesoft_sn = match.group(0)
                log.info(f"  EEPROM serienummer funne: {dewesoft_sn}")
                self._enhetsinfo.serienummer = dewesoft_sn
            else:
                log.info("  EEPROM: Dewesoft-serienummer ikkje funne (held USB-serial)")

        # Flush dataendepunkt
        log.info("  Flush endepunkt...")
        for ep in [EP_ADC_IN, EP_CTRL_IN, EP_SYNC_IN]:
            proto.flush_endepunkt(ep)

        log.info(f"Init fullfoert: {enkel_teller} enkle + {ad_teller} AD "
                 f"= {enkel_teller + ad_teller} kommandoar ({feil_teller} feil)")

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
        SiriusDriver._orphan_kill.clear()  # Nullstill i tilfelle førre rekoble sette det
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
                data = list(self._data_buffer)[:antall_rammer]
                for _ in range(min(antall_rammer, len(self._data_buffer))):
                    self._data_buffer.popleft()
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

        while not self._stopp_event.is_set() and not SiriusDriver._orphan_kill.is_set():
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

                    # Buffer (deque med maxlen handterer eviction automatisk)
                    with self._buffer_lock:
                        self._data_buffer.append(kanal_data)

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
        # Stopp midlertidig B1-pumpe frå koble_til() viss den framleis køyrer.
        # Pumpa heldt EP2 i live mellom koble_til() og start_streaming().
        # No tek heartbeat-tråden over — det MÅ ikkje vere gap.
        if self._temp_hb_stopp is not None:
            self._temp_hb_stopp.set()
            if self._temp_hb_traad and self._temp_hb_traad.is_alive():
                self._temp_hb_traad.join(timeout=2)
            self._temp_hb_stopp = None
            self._temp_hb_traad = None
            log.info("Heartbeat-tråd tok over frå temp B1-pumpe")

        feil_teller = 0
        poll_teller = 0
        ae_intervall = 10  # Send AE kvar 10. B1-poll (~10 Hz ved ~100 polls/sek)
        log.info("Heartbeat-tråd starta")
        while not self._stopp_event.is_set() and not SiriusDriver._orphan_kill.is_set():
            try:
                # Hovud-heartbeat: B1 poll med kort timeout
                self._proto.poll_b1(timeout=10)
                feil_teller = 0
                poll_teller += 1

                # Drain EP4/EP6 — SIRIUS krev at ALLE data-endepunkt vert
                # lesne samstundes.  Utan dette fylst EP4/EP6-FIFO og EP2
                # stoppar.  pcapng viser at DewesoftX les EP2+EP4+EP6 parallelt.
                # EP6 er 15872B/pkt (same som EP2), EP4 er 20B/pkt.
                for ep, sz in [(EP_CTRL_IN, 64), (EP_SYNC_IN, 16384)]:
                    try:
                        self._dev.read(ep, sz, timeout=1)
                    except Exception:
                        pass

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

        # Deinterlev med numpy reshape (raskare enn per-kanal slice)
        n_frames = len(alle) // antall_kanaler
        trim = n_frames * antall_kanaler
        interlev = alle[:trim]
        reshaped = interlev.reshape(n_frames, antall_kanaler).T

        resultat = {}
        for k in range(antall_kanaler):
            resultat[f'kanal_{k}'] = reshaped[k].copy()

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
