#!/usr/bin/env python3
"""
openDAQ Nettverksbro - DewesoftX-tilkobling via openDAQ-servere
================================================================
Startar openDAQ Instance med referanse-enhet som ROOT og standard
servere (OPC-UA, Native Streaming, WebSocket) slik at DewesoftX kan
koble til via openDAQ-protokollen.

Fase 1: Referanse-enhet (daqref://device0) med simulerte kanalar
Fase 2: Reelle SIRIUS-data injisert via kanal-eigenskapar (Amplitude/Offset)

Bruk:
    from opendaq_bro import OpenDAQBro

    bro = OpenDAQBro()
    bro.start()
    print(bro.hent_status())
    # ...
    bro.stopp()
"""

import os
import socket
import logging
import threading
from datetime import datetime

import numpy as np

from kanal_konfig import les_konfig

log = logging.getLogger('opendaq_bro')

# Grasioes openDAQ-import
_daq = None
_daq_import_feil = None
try:
    import opendaq as _daq
except Exception as e:
    _daq_import_feil = str(e)
    log.warning(f"opendaq Python-bindingar ikkje tilgjengelege: {e}")


class OpenDAQBro:
    """
    openDAQ nettverksbro.

    Opprettar openDAQ Instance med referanse-enhet som ROOT DEVICE
    og startar OPC-UA + Native Streaming + WebSocket servere.
    DewesoftX finn eininga via openDAQ mDNS-oppdaging.
    """

    def __init__(self, module_path=None, serienummer="", enhetsnamn=""):
        self._instance = None
        self._device = None
        self._tilgjengelig = False
        self._module_path = module_path or os.environ.get(
            "OPENDAQ_MODULE_PATH", "/usr/local/lib"
        )
        self._serienummer = serienummer
        self._enhetsnamn = enhetsnamn
        self._lock = threading.Lock()
        self._kanal_signal = []     # Liste av (channel, signal) tupler for data-injeksjon
        self._kanal_skala = []      # Skaleringsfaktor per kanal: physical = raw_int16 * skala
        self._siste_verdiar = {}    # Siste verdi per kanal for live-visning i web UI
        self._data_teller = 0       # Totalt antal datapunkt motteke
        self._sirius_aktiv = False  # True når reell SIRIUS-data strøymer
        self._sirius_ts = 0.0       # Tidsstempel for siste SIRIUS-data
        self._leser_traad = None
        self._stopp_event = threading.Event()
        self._status = {
            "tilgjengelig": False,
            "aktiv": False,
            "enhet_namn": "",
            "tilkobling": "",
            "kanalar": [],
            "servere": [],
            "ip": "",
            "porter": {
                "opcua": 4840,
                "native_streaming": 7420,
                "websocket": 7414,
            },
            "startet": None,
            "feil": None,
        }

    @property
    def tilgjengelig(self) -> bool:
        return self._tilgjengelig

    def hent_status(self) -> dict:
        """Returner status-dict for web API."""
        with self._lock:
            return dict(self._status)

    def start(self) -> bool:
        """Start openDAQ instance med root device, deretter servere."""
        if _daq is None:
            feil = f"opendaq ikkje tilgjengeleg: {_daq_import_feil or 'import feila'}"
            with self._lock:
                self._status["feil"] = feil
            log.warning(f"openDAQ bridge: {feil}")
            return False

        try:
            log.info("Startar openDAQ nettverksbro...")
            log.info(f"  Modulsti: {self._module_path}")

            # Bruk add_device() i staden for set_root_device().
            # Med set_root_device er referanse-eininga rota, og getDomain()
            # på den kastar C++ exception som krasjar DewesoftX:
            #   "External exception E06D7363 at GetDomain"
            # Med add_device er openDAQ Instance rota (gyldig domain),
            # og referanse-eininga er ei sub-eining under den.
            builder = _daq.InstanceBuilder()
            builder.add_module_path(self._module_path)

            # Sett enhetsinfo (serienummer, MAC, produsent) paa root instance.
            # MÅ gjerast FØR build() — etterpaa er DeviceInfo frozen/umuterleg.
            try:
                mac = self._hent_mac()
                dev_info = _daq.DeviceInfoConfig(
                    self._enhetsnamn or "PQTech SIRIUS Bridge",
                    "daqref://device0"
                )
                if self._serienummer:
                    dev_info.serial_number = self._serienummer
                dev_info.manufacturer = "Dewesoft / PQTech"
                dev_info.model = "SIRIUSi-HS"
                dev_info.mac_address = mac
                dev_info.platform = "RaspberryPi"
                builder.default_root_device_info = dev_info
                log.info(f"  DeviceInfo: sn={self._serienummer}, mac={mac}")
            except Exception as e:
                log.warning(f"  DeviceInfo-konfig feilet (ikkje kritisk): {e}")

            self._instance = builder.build()
            log.info("  Instance oppretta")

            # Legg til referanse-eining som sub-device
            self._device = self._instance.add_device("daqref://device0")
            enhet_namn = ""
            try:
                enhet_namn = self._device.name if hasattr(self._device, 'name') else str(self._device)
            except Exception:
                enhet_namn = "RefDevice0"
            log.info(f"  Referanse-enhet (sub-device): {enhet_namn}")

            # Diagnostikk: List tilgjengelege eigenskapar på referanse-eininga
            try:
                props = self._device.visible_properties
                prop_names = [p.name for p in props]
                log.info(f"  Device-eigenskapar: {prop_names}")
            except Exception as e:
                log.warning(f"  Kunne ikkje liste eigenskapar: {e}")

            # Konfigurer 8 kanalar som matchar SIRIUS Sundet-oppsett
            self._konfig_kanalar()

            # Hent signal-referansar frå kvar kanal for data-injeksjon
            self._kanal_signal = []
            try:
                for ch in self._device.channels:
                    sigs = list(ch.signals)
                    if sigs:
                        self._kanal_signal.append((ch, sigs[0]))
                        # Diagnostikk: logg signal-info for fyrste kanal
                        if len(self._kanal_signal) == 1:
                            sig = sigs[0]
                            try:
                                desc = sig.descriptor
                                log.info(f"  Signal[0] namn={sig.name}, "
                                         f"descriptor.name={desc.name if desc else 'None'}, "
                                         f"sample_type={desc.sample_type if desc else 'None'}")
                            except Exception:
                                log.info(f"  Signal[0] namn={sig.name} (ingen descriptor)")
                    else:
                        self._kanal_signal.append((ch, None))
                log.info(f"  Signal-referansar: {len(self._kanal_signal)} kanalar, "
                         f"{sum(1 for _, s in self._kanal_signal if s is not None)} med signal")
            except Exception as e:
                log.warning(f"  Signal-henting feilet: {e}")

            # List kanalar
            kanalar = []
            try:
                for ch in self._device.channels:
                    namn = ch.name if hasattr(ch, 'name') else str(ch)
                    kanalar.append(namn)
                log.info(f"  Kanalar: {len(kanalar)}")
            except Exception as e:
                log.warning(f"  Kanallisting feilet: {e}")

            # Start servere på instance (som er rota).
            # Inkluderer Native Streaming som DewesoftX treng for full
            # NewSetup-forhandling.
            servere = []
            for srv_type in ['OpenDAQOPCUA', 'OpenDAQNativeStreaming',
                             'OpenDAQLTStreaming', 'OpenDAQNewLTStreaming']:
                try:
                    self._instance.add_server(srv_type, None)
                    servere.append(srv_type)
                    log.info(f"  Server: {srv_type}")
                except Exception as e2:
                    log.warning(f"  {srv_type} feilet: {e2}")

            ip = self._hent_ip()

            # Fiks tomme server capability-adresser.
            # openDAQ sin mDNS-baserte interface-oppdaging fungerer ikkje
            # paalidelig i Docker (fleire bridge-interface). Sett adresser
            # manuelt slik at DewesoftX-klienten finn streaming-endepunkta.
            self._fiks_server_capabilities(ip)

            with self._lock:
                self._status.update({
                    "tilgjengelig": True,
                    "aktiv": True,
                    "enhet_namn": enhet_namn,
                    "tilkobling": "daqref://device0",
                    "kanalar": kanalar,
                    "servere": servere,
                    "ip": ip,
                    "startet": datetime.now().isoformat(),
                    "feil": None,
                })

            self._tilgjengelig = True

            # Logg hostname-oppslag (viktig for OPC-UA endpoint URL)
            try:
                import socket as _sock
                hostname = _sock.gethostname()
                resolved = _sock.gethostbyname(hostname)
                log.info(f"  Hostname: {hostname} -> {resolved}")
                if resolved.startswith("127."):
                    log.warning(f"  ADVARSEL: hostname resolver til {resolved}!")
                    log.warning("  OPC-UA vil annonsere localhost. Fiks /etc/hosts.")
            except Exception:
                pass

            # Start bakgrunnstraad for å lese signal-verdiar (for web UI)
            self._stopp_event.clear()
            self._leser_traad = threading.Thread(
                target=self._les_signal_loop, daemon=True
            )
            self._leser_traad.start()

            log.info("")
            log.info("  openDAQ nettverksbro aktiv:")
            log.info(f"    OPC-UA:           {ip}:4840")
            log.info(f"    Native Streaming: {ip}:7420")
            log.info(f"    WebSocket:        {ip}:7414")
            log.info(f"    DewesoftX: HW Settings > + > openDAQ device")
            log.info("")
            return True

        except Exception as e:
            feil_msg = str(e)
            log.error(f"openDAQ bridge feilet: {feil_msg}")
            with self._lock:
                self._status["feil"] = feil_msg
            return False

    def oppdater_enhetsinfo(self, serienummer="", enhetsnamn=""):
        """Oppdater serienummer/enhetsnamn etter oppstart (t.d. naar driver koplar til).

        Prøver set_property_value paa instance.info — fungerer berre viss
        DeviceInfo ikkje er fullstendig frozen.
        """
        if not self._instance:
            return
        try:
            info = self._instance.info
            if serienummer:
                info.set_property_value("serialNumber", serienummer)
                log.info(f"  DeviceInfo oppdatert: serialNumber={serienummer}")
            if enhetsnamn:
                info.set_property_value("name", enhetsnamn)
        except Exception as e:
            log.debug(f"  oppdater_enhetsinfo: set_property_value feilet (forventa viss frozen): {e}")

    def oppdater_data(self, kanal_data):
        """
        Injiser ADC-data frå SIRIUS inn i openDAQ-signalar.

        Oppdaterer kanal-eigenskapar (Amplitude, Offset) basert på reelle
        ADC-verdiar slik at referanse-eininga reflekterer faktiske maaleverdiar.
        Lagrar ogsaa siste verdiar for live-visning i web UI.

        Args:
            kanal_data: dict {"kanal_0": np.array(int16), ...} fraa SiriusDriver
        """
        if not self._tilgjengelig or not self._kanal_signal:
            return

        import time
        self._sirius_aktiv = True
        self._sirius_ts = time.time()

        try:
            for kanal_idx, (key, data) in enumerate(sorted(kanal_data.items())):
                if kanal_idx >= len(self._kanal_signal):
                    break

                ch, sig = self._kanal_signal[kanal_idx]
                if data is None or len(data) == 0:
                    continue

                # Skaler int16 ADC-verdiar til fysiske einingar (V/A)
                skala = (self._kanal_skala[kanal_idx]
                         if kanal_idx < len(self._kanal_skala) else 1.0)
                fdata = data.astype(np.float64) * skala

                # Berekn statistikk i fysiske einingar
                snitt = float(np.mean(fdata))
                rms = float(np.sqrt(np.mean(fdata ** 2)))
                topp = float(np.max(np.abs(fdata)))

                # Lagre siste verdiar for web UI
                self._siste_verdiar[key] = {
                    "snitt": round(snitt, 4),
                    "rms": round(rms, 4),
                    "topp": round(topp, 4),
                    "siste": round(float(data[-1]) * skala, 4),
                    "antall": len(data),
                    "kjelde": "sirius",
                }

                # Oppdater referanse-eininga sine kanal-eigenskapar
                # slik at genererte signal matchar reelle verdiar.
                # Amplitude: FloatProperty [0, 10] — klampa av _safe_set
                # DC:        FloatProperty [-10, 10] — brukt for DC-offset
                #            (ikkje "Offset" som er IntProperty for sample-offset)
                try:
                    self._safe_set(ch, "Amplitude", topp)
                    self._safe_set(ch, "DC", snitt)
                except Exception:
                    pass

            self._data_teller += 1

        except Exception as e:
            if self._data_teller % 100 == 0:
                log.warning(f"oppdater_data feil: {e}")

    def hent_siste_verdiar(self) -> dict:
        """Returner siste kanal-verdiar for web UI live-visning."""
        result = dict(self._siste_verdiar)
        result["_debug"] = {
            "data_teller": self._data_teller,
            "sirius_aktiv": self._sirius_aktiv,
            "sirius_ts": self._sirius_ts,
        }
        return result

    def _les_signal_loop(self):
        """Bakgrunnstraad som genererer simulerte verdiar.

        Brukar SUNDET_KANALAR-konfig direkte (ikkje openDAQ-eigenskapar)
        for å vise realistiske simulerte verdiar i web UI.
        Hopper over når SIRIUS leverer reelle data.
        """
        import time
        import math
        log.info("  Signal-leser-traad starta")

        t0 = time.time()
        while not self._stopp_event.is_set():
            # Sjekk om SIRIUS er aktiv (data motteke siste 5 sekund)
            sirius_nyleg = (
                self._sirius_aktiv and
                (time.time() - self._sirius_ts) < 5.0
            )

            if not sirius_nyleg:
                self._sirius_aktiv = False
                t = time.time() - t0
                # Generer simulerte verdiar frå SUNDET_KANALAR
                for i, cfg in enumerate(self.SUNDET_KANALAR):
                    key = f"kanal_{i}"
                    amp = cfg["amplitude"]
                    freq = cfg["freq"]
                    # Forskyv fase per kanal (120° mellom fasane)
                    faseforskyvning = 0.0
                    if i < 3:
                        faseforskyvning = i * (2.0 * math.pi / 3.0)  # L1, L2, L3
                    elif 4 <= i <= 6:
                        faseforskyvning = (i - 4) * (2.0 * math.pi / 3.0)

                    verdi = amp * math.sin(2 * math.pi * freq * t + faseforskyvning)
                    rms = amp / math.sqrt(2) if amp > 0 else 0.0
                    self._siste_verdiar[key] = {
                        "snitt": 0.0,
                        "rms": round(rms, 2),
                        "topp": round(abs(amp), 2),
                        "siste": round(verdi, 2),
                        "antall": 100,
                        "kjelde": "simulert",
                    }

            self._stopp_event.wait(timeout=1.0)

        log.info("  Signal-leser-traad stoppa")

    def stopp(self):
        """Stopp openDAQ instance og servere."""
        self._tilgjengelig = False
        self._stopp_event.set()
        if self._leser_traad and self._leser_traad.is_alive():
            self._leser_traad.join(timeout=3)
        with self._lock:
            self._status["aktiv"] = False
        # Instance-opprydding handtert av Python GC
        self._instance = None
        self._device = None
        log.info("openDAQ nettverksbro stoppa")

    # Kanalopsett (SIRIUSi-HS, 4×Hi-LV + 4×Lo-LV)
    # AI 1-3: Hi-LV, spenning 230V RMS (325V topp), 50 Hz
    # AI 4:   Hi-LV, ikkje tilkobla
    # AI 5-7: Lo-LV, integrator 0-3V → 0-6000A (5V-driven)
    #         Lo-LV ADC ±5V, faktor 2000 A/V → range ±10000A
    # AI 8:   Lo-LV, ikkje tilkobla
    SUNDET_KANALAR = [
        {"namn": "AI 1", "amplitude": 325.0, "freq": 50.0, "range": (-1600, 1600)},
        {"namn": "AI 2", "amplitude": 325.0, "freq": 50.0, "range": (-1600, 1600)},
        {"namn": "AI 3", "amplitude": 325.0, "freq": 50.0, "range": (-1600, 1600)},
        {"namn": "AI 4", "amplitude": 0.0,   "freq": 50.0, "range": (-1600, 1600)},
        {"namn": "AI 5", "amplitude": 100.0, "freq": 50.0, "range": (-10000, 10000)},
        {"namn": "AI 6", "amplitude": 100.0, "freq": 50.0, "range": (-10000, 10000)},
        {"namn": "AI 7", "amplitude": 100.0, "freq": 50.0, "range": (-10000, 10000)},
        {"namn": "AI 8", "amplitude": 0.0,   "freq": 50.0, "range": (-10000, 10000)},
    ]

    def _konfig_kanalar(self):
        """Sett 8 kanalar med namn og eigenskapar frå kanal_konfig (eller fallback til SUNDET).

        openDAQ referanse-eininga har faste grenser for eigenskapar:
          - Amplitude: Float, [0, 10]
          - Frequency: Float, [0.1, 10000]
          - DC:        Float, [-10, 10]
          - Waveform:  Int (Selection), 0=Sine
          - Offset:    Int, min=0 (sample-offset, ikkje DC)
          - GlobalSampleRate: Float, [1, 1000000]
        Verdiar vert klampa automatisk av _safe_set().
        """
        # NumberOfChannels er IntProperty — int er korrekt
        if not self._safe_set(self._device, "NumberOfChannels", 8):
            log.warning("  Kunne ikkje sette NumberOfChannels — avbryt kanalkonfig")
            return
        log.info("  NumberOfChannels sett til 8")

        # GlobalSampleRate er FloatProperty — MÅ vere float, ikkje int!
        if self._safe_set(self._device, "GlobalSampleRate", 1000.0):
            log.info("  GlobalSampleRate sett til 1000.0 Hz")

        # Logg device-eigenskapar for feilsøking
        self._logg_eigenskapar(self._device, "Dev.")

        # Les persistert konfig (fallback til standard)
        kanal_konfig = les_konfig()

        # Bygg skaleringsfaktorar: SIRIUS ADC leverer int16 (-32768..32767)
        # som representerer full skala av input-rangen.
        # physical_value = raw_int16 * (range_max / 32768)
        self._kanal_skala = []

        channels = list(self._device.channels)
        for i, ch in enumerate(channels):
            if i >= len(kanal_konfig):
                break
            kk = kanal_konfig[i]
            # Bruk SUNDET_KANALAR for amplitude/freq som fallback
            sundet = self.SUNDET_KANALAR[i] if i < len(self.SUNDET_KANALAR) else None
            try:
                ch.name = kk.namn

                amp = sundet["amplitude"] if sundet else 0.0
                freq = sundet["freq"] if sundet else 50.0

                # Amplitude er FloatProperty [0, 10] — _safe_set klampar
                self._safe_set(ch, "Amplitude", amp if kk.aktiv else 0.0)
                # Frequency er FloatProperty [0.1, 10000]
                self._safe_set(ch, "Frequency", freq)
                # DC er FloatProperty [-10, 10]
                self._safe_set(ch, "DC", 0.0)
                # Waveform er SelectionProperty (int): 0=Sine
                self._safe_set(ch, "Waveform", 0)
                # CustomRange er StructProperty (Range)
                ch.set_property_value("CustomRange",
                                      _daq.Range(kk.range_min, kk.range_max))
                log.info(f"  {kk.namn}: aktiv={kk.aktiv}, "
                         f"range=[{kk.range_min}, {kk.range_max}], type={kk.type}")
            except Exception as e:
                log.warning(f"  Kanal {i} konfig feilet: {e}")

            # Skaleringsfaktor: int16 → fysisk eining
            skala = kk.range_max / 32768.0 if kk.range_max > 0 else 1.0
            self._kanal_skala.append(skala)

        # Logg fyrste kanal sine eigenskapar for å verifisere type-matching
        if channels:
            self._logg_eigenskapar(channels[0], "Ch0.")

    def _fiks_server_capabilities(self, ip):
        """Sett server capability-adresser manuelt for Docker-miljoe.

        Overskriver ALLTID, ogsaa pre-populerte verdiar. openDAQ sin
        mDNS-oppdaging kan sette feil IP (Docker bridge 172.17.x.x)
        som DewesoftX ikkje kan naa.

        Addresses og ConnectionStrings er ListProperty<IString> — krev
        openDAQ List-objekt, ikkje Python-liste.
        """
        try:
            # Server capabilities er på instance (root), ikkje sub-device
            info = self._instance.info
            caps = info.server_capabilities
            for cap in caps:
                proto_id = cap.protocol_id
                prefix = cap.prefix
                port = cap.port

                # Bygg connection string: daq.ns://ip:port/
                ny_conn = f"{prefix}://{ip}:{port}/"

                # PrimaryConnectionString er StringProperty
                self._safe_set(cap, "PrimaryConnectionString", ny_conn)
                log.info(f"  Cap {proto_id}: PrimaryConnectionString = {ny_conn}")

                # Addresses og ConnectionStrings er ListProperty<IString>.
                # Bygg openDAQ List-objekt i staden for Python-liste.
                try:
                    addr_list = _daq.List()
                    addr_list.push_back(ip)
                    cap.set_property_value("Addresses", addr_list)
                    log.info(f"  Cap {proto_id}: Addresses = [{ip}]")
                except Exception as e:
                    # Fallback: prøv Python-liste
                    try:
                        cap.set_property_value("Addresses", [ip])
                    except Exception:
                        pass
                    log.warning(f"  Cap {proto_id}: set Addresses feilet: {e}")

                try:
                    conn_list = _daq.List()
                    conn_list.push_back(ny_conn)
                    cap.set_property_value("ConnectionStrings", conn_list)
                except Exception as e:
                    try:
                        cap.set_property_value("ConnectionStrings", [ny_conn])
                    except Exception:
                        pass
                    log.warning(f"  Cap {proto_id}: set ConnectionStrings feilet: {e}")

        except Exception as e:
            log.warning(f"  Fiks server capabilities feilet: {e}")

    def _safe_set(self, obj, namn, verdi):
        """Sett eigenskap med automatisk type-konvertering og range-klamping.

        openDAQ 3.20.6 sitt OPC-UA-lag er strikt på typar:
          - FloatProperty krev Python float (ikkje int)
          - IntProperty krev Python int (ikkje float)
          - Verdiar utanfor [min, max] vert avvist
        Denne metoden les eigenskapen sin deklarerte type og konverterer.
        """
        try:
            prop = obj.get_property(namn)
            vtype = prop.value_type

            ct = getattr(_daq, 'CoreType', None)
            if ct is not None:
                if vtype == ct.ctFloat:
                    v = float(verdi)
                    try:
                        v = max(v, float(prop.min_value))
                    except Exception:
                        pass
                    try:
                        v = min(v, float(prop.max_value))
                    except Exception:
                        pass
                    obj.set_property_value(namn, v)
                    return True

                if vtype == ct.ctInt:
                    v = int(round(verdi)) if isinstance(verdi, float) else int(verdi)
                    try:
                        v = max(v, int(prop.min_value))
                    except Exception:
                        pass
                    try:
                        v = min(v, int(prop.max_value))
                    except Exception:
                        pass
                    obj.set_property_value(namn, v)
                    return True

                if vtype == ct.ctBool:
                    obj.set_property_value(namn, bool(verdi))
                    return True

                if vtype == ct.ctString:
                    obj.set_property_value(namn, str(verdi))
                    return True

            # Fallback: set direkte (ingen CoreType tilgjengeleg)
            obj.set_property_value(namn, verdi)
            return True
        except Exception as e:
            log.warning(f"  _safe_set({namn}, {verdi!r}): {e}")
            return False

    def _logg_eigenskapar(self, obj, label=""):
        """Logg alle synlege eigenskapar med type og verdi (for feilsøking)."""
        try:
            ct = getattr(_daq, 'CoreType', None)
            ct_namn = {getattr(ct, a): a for a in dir(ct)
                       if a.startswith('ct')} if ct else {}
            for prop in obj.visible_properties:
                try:
                    vt = prop.value_type
                    vt_str = ct_namn.get(vt, str(vt))
                    val = obj.get_property_value(prop.name)
                    extra = ""
                    try:
                        extra = f" [{prop.min_value}..{prop.max_value}]"
                    except Exception:
                        pass
                    log.info(f"  {label}{prop.name}: {vt_str} = {val!r}{extra}")
                except Exception as e2:
                    log.info(f"  {label}{prop.name}: (feil: {e2})")
        except Exception as e:
            log.warning(f"  _logg_eigenskapar({label}): {e}")

    @staticmethod
    def _hent_ip():
        """Finn maskinens IP-adresse."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def _hent_mac():
        """Finn MAC-adresse for fyrste ikkje-loopback nettverksinterface.

        Fungerer paa Linux (Raspberry Pi) via /sys/class/net/.
        Fallback til uuid.getnode() på andre plattformer.
        """
        import pathlib
        net_dir = pathlib.Path("/sys/class/net")
        if net_dir.exists():
            for iface in sorted(net_dir.iterdir()):
                if iface.name == "lo":
                    continue
                addr_file = iface / "address"
                if addr_file.exists():
                    mac = addr_file.read_text().strip()
                    if mac and mac != "00:00:00:00:00:00":
                        return mac
        # Fallback
        import uuid
        raw = uuid.getnode()
        return ':'.join(f'{(raw >> (8 * i)) & 0xFF:02x}' for i in range(5, -1, -1))
