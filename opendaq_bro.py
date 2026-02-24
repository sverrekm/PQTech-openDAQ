#!/usr/bin/env python3
"""
openDAQ Nettverksbro - DewesoftX-tilkobling via openDAQ-servere
================================================================
Startar openDAQ Instance med referanse-enhet som ROOT og standard
servere (OPC-UA, Native Streaming, WebSocket) slik at DewesoftX kan
koble til via openDAQ-protokollen.

Fase 1: Referanse-enhet (daqref://device0) med simulerte kanalar
Fase 2: Reelle SIRIUS-data injisert via DataPacket + send_packet()

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
        self._kanal_offset = []     # Offset per kanal (for sensor-skalering)
        self._siste_verdiar = {}    # Siste verdi per kanal for live-visning i web UI
        self._data_teller = 0       # Totalt antal datapunkt motteke
        self._sirius_aktiv = False  # True når reell SIRIUS-data strøymer
        self._sirius_ts = 0.0       # Tidsstempel for siste SIRIUS-data
        self._leser_traad = None
        self._stopp_event = threading.Event()
        # DataPacket-injeksjon (fase 2: reelle SIRIUS-data)
        self._dom_signal = []      # Domain signal per kanal
        self._dom_desc = []        # Domain descriptor per kanal
        self._val_desc = []        # Value descriptor per kanal
        self._total_samples = []   # Total samples sendt per kanal
        self._tick_delta = 50      # Ticks per sample (1MHz / 20kHz)
        self._start_ticks = 0      # Starttid i ticks
        self._pakett_klar = False  # True når pakett-injeksjon er klar
        # Pre-allokert buffer for float64-konvertering (992 frames = typisk EP2-pakke)
        self._fdata_buf = np.empty(992, dtype=np.float64)
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

            # Bruk set_root_device() slik at referanse-eininga ER rota.
            # DewesoftX får direkte tilgang utan sub-device-wrapping.
            # getDomain() nil-krasj fiksa med C++ patch i Dockerfile.
            builder = _daq.InstanceBuilder()
            builder.add_module_path(self._module_path)

            # DeviceInfo og DeviceType er sett via C++ patch i Dockerfile
            # (Patch 3: ref_device_impl.cpp). Python-bindingane har ikkje
            # set_default_root_device_info(), so alt skjer i C++-kjelda.

            # mDNS discovery server: annonserer eininga på nettverket slik at
            # DewesoftX finn den automatisk (same som offisielt eksempel).
            builder.add_discovery_server("mdns")

            builder.set_root_device("daqref://device0")
            self._instance = builder.build()
            self._device = self._instance  # Instance wraps root device
            log.info("  Instance oppretta med root device (daqref://device0)")

            enhet_namn = ""
            try:
                enhet_namn = self._device.name if hasattr(self._device, 'name') else str(self._device)
            except Exception:
                enhet_namn = "RefDevice0"
            log.info(f"  Root device: {enhet_namn}")

            # DeviceInfo er sett via C++ Patch 3 i Dockerfile.
            # Python kan ikkje endre frosen DeviceInfo — hopp over.
            # self._sett_sirius_device_info()

            # Diagnostikk: List tilgjengelege eigenskapar på referanse-eininga
            try:
                props = self._device.visible_properties
                prop_names = [p.name for p in props]
                log.info(f"  Device-eigenskapar: {prop_names}")
            except Exception as e:
                log.warning(f"  Kunne ikkje liste eigenskapar: {e}")

            # Konfigurer 9 kanalar (8 ADC + 1 tid) som matchar SIRIUS Sundet-oppsett
            self._konfig_kanalar()

            # Legg til GetPossibleSampleRate eigenskapen.
            # DewesoftX sin TDSOpenDaqAI.CalcADCSampleRate spør etter denne
            # eigenskapen. Utan den feilar det med 0x80000006 og kanalane
            # viser "Invalid or no data". Legg til på device og kvar kanal.
            self._legg_til_sample_rate_eigenskap()

            # Legg til eigenskapar DewesoftX si settings-side forventer
            # (hindrar Access Violation i TOpenDAQFrame.Initialize)
            self._legg_til_dewesoftx_eigenskapar()

            # Fiks nil string-eigenskapar som krasjar DewesoftX
            self._fiks_alle_nil_strings()

            # Logg DeviceInfo for feilsøking
            try:
                info = self._device.info
                log.info(f"  DeviceInfo:")
                log.info(f"    name={info.name}")
                for attr in ['model', 'manufacturer', 'serial_number']:
                    try:
                        val = getattr(info, attr, '?')
                        log.info(f"    {attr}={val}")
                    except Exception:
                        log.info(f"    {attr}=(feil)")
            except Exception as e:
                log.warning(f"  DeviceInfo tilgang feilet: {e}")

            # Hent signal-referansar frå kvar kanal for data-injeksjon
            # NB: ch.signals returnerer ISignal (read-only), men send_packet()
            # krev ISignalConfig. Cast via _daq.ISignalConfig.cast_from().
            # VIKTIG: ISignalConfig har berre setter for domain_signal/descriptor,
            # so me lagrar BÅDE ISignal (for lesing) og ISignalConfig (for sending).
            self._kanal_signal = []     # (ch, ISignalConfig) for send_packet
            self._kanal_signal_ro = []  # (ch, ISignal) for lesing av descriptor/domain
            try:
                for ch in self._device.channels:
                    sigs = list(ch.signals)
                    if sigs:
                        raw_sig = sigs[0]  # ISignal (read-only)
                        # Cast til ISignalConfig for send_packet()-tilgang
                        try:
                            sig_config = _daq.ISignalConfig.cast_from(raw_sig)
                        except Exception:
                            sig_config = raw_sig  # fallback
                        self._kanal_signal.append((ch, sig_config))
                        self._kanal_signal_ro.append((ch, raw_sig))
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
                        self._kanal_signal_ro.append((ch, None))
                log.info(f"  Signal-referansar: {len(self._kanal_signal)} kanalar, "
                         f"{sum(1 for _, s in self._kanal_signal if s is not None)} med signal")
            except Exception as e:
                log.warning(f"  Signal-henting feilet: {e}")

            # Initialiser DataPacket-injeksjon for reelle SIRIUS-data
            self._init_pakett_injeksjon()

            # Sett signal descriptors med einingar og metadata (Nivå 1)
            self._sett_signal_descriptors()

            # List kanalar og sjekk domain-signal (nil → DewesoftX krasj)
            kanalar = []
            try:
                for idx, ch in enumerate(self._device.channels):
                    namn = ch.name if hasattr(ch, 'name') else str(ch)
                    kanalar.append(namn)
                    # Sjekk at kvart signal har domain-signal (kritisk for DewesoftX)
                    try:
                        for sig in ch.signals:
                            dom = sig.domain_signal
                            if dom is None:
                                log.warning(f"  ADVARSEL: Ch{idx}/{sig.name} "
                                            f"har INGEN domain-signal!")
                            else:
                                dom_desc = dom.descriptor
                                if dom_desc is None:
                                    log.warning(f"  ADVARSEL: Ch{idx}/{sig.name} "
                                                f"domain-signal manglar descriptor!")
                    except Exception as e2:
                        log.warning(f"  Ch{idx} domain-sjekk feilet: {e2}")
                log.info(f"  Kanalar: {len(kanalar)}")
            except Exception as e:
                log.warning(f"  Kanallisting feilet: {e}")

            # Start servere på instance (som er rota).
            # VIKTIG rekkefølge: Streaming-servere FYRST, OPC-UA SIST.
            # OPC-UA publiserer streaming capabilities og MÅ startast etter
            # at streaming-serverane er oppe (openDAQ-dokumentasjon).
            servere = []

            # Diagnostikk: list tilgjengelege server-typar og deira konfig
            try:
                tilgjengelege = self._instance.available_server_types
                log.info(f"  Tilgjengelege server-typar: {list(tilgjengelege.keys())}")
                for srv_name in tilgjengelege:
                    try:
                        srv_type_obj = tilgjengelege[srv_name]
                        cfg = srv_type_obj.create_default_config()
                        props = []
                        for p in cfg.visible_properties:
                            try:
                                val = cfg.get_property_value(p.name)
                                props.append(f"{p.name}={val!r}")
                            except Exception:
                                props.append(f"{p.name}=?")
                        log.info(f"  Konfig {srv_name}: {', '.join(props) or '(ingen)'}")
                    except Exception:
                        log.info(f"  Konfig {srv_name}: ikkje tilgjengeleg")
            except Exception as e:
                log.warning(f"  Kunne ikkje liste server-typar: {e}")

            ip = self._hent_ip()

            # ============================================================
            # Server-oppstart i 3 fasar:
            #   1) add_server() — start servere og bind portar
            #   2) _fiks_primary_connection_strings() — sett riktig IP
            #   3) enable_discovery() — publiser mDNS
            #
            # Fase 2 er naudsynt når Pi har fleire nettverksinterface
            # (WiFi + LAN) — utan dette kan NativeStreaming peike til
            # feil IP og DewesoftX feilar AddDevice.
            # ============================================================

            # Fase 1: Legg til servere (bind portar)
            # NativeStreaming FYRST (data), deretter OPC-UA SIST (konfig).
            # DewesoftX brukar mDNS _opcua-tcp._tcp for oppdaging og krev
            # OPC-UA for konfigurasjon. NativeStreaming handterer data.
            # OPC-UA MÅ startast ETTER streaming (openDAQ-dokumentasjon).
            # LTStreaming utelatt — unødvendig og kan forårsake duplikat.
            servers_added = []
            for srv_type in ['OpenDAQNativeStreaming', 'OpenDAQOPCUA']:
                try:
                    config = None
                    try:
                        srv_type_obj = self._instance.available_server_types.get(srv_type)
                        if srv_type_obj:
                            config = srv_type_obj.create_default_config()
                    except Exception:
                        config = None

                    server = self._instance.add_server(srv_type, config)
                    servers_added.append((srv_type, server))
                    servere.append(srv_type)
                    log.info(f"  Server: {srv_type}")
                except Exception as e2:
                    import traceback
                    log.warning(f"  {srv_type} feilet: {e2}")
                    log.warning(f"  {srv_type} traceback: {traceback.format_exc()}")

            # Verifiser at OPC-UA faktisk lyttar (port 4840).
            # IKKJE probe NativeStreaming (port 7420) — TCP connect/disconnect
            # forårsakar "Failed to read connect request headers" og kan
            # opprette ein stale "exclusive control"-sesjon som blokkerer
            # DewesoftX si daq.nd:// tilkopling.
            import socket as _sock
            probe_host = ip or os.environ.get("OPENDAQ_IP", "127.0.0.1")
            faktisk_aktive = []
            for srv_type in list(servere):
                if srv_type == 'OpenDAQOPCUA':
                    try:
                        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                        s.settimeout(1.0)
                        s.connect((probe_host, 4840))
                        s.close()
                        faktisk_aktive.append(srv_type)
                    except (ConnectionRefusedError, OSError):
                        log.warning(f"  {srv_type} rapporterte OK men lyttar "
                                    f"IKKJE på port 4840!")
                        servere.remove(srv_type)
                else:
                    # NativeStreaming: stol på add_server() — ikkje TCP-probe
                    faktisk_aktive.append(srv_type)
            log.info(f"  Verifiserte serverar: {faktisk_aktive}")

            # Logg server capabilities FØR fiks (diagnostikk)
            try:
                for cap in self._instance.info.server_capabilities:
                    try:
                        props_info = []
                        for p in cap.visible_properties:
                            try:
                                v = cap.get_property_value(p.name)
                                props_info.append(f"{p.name}={v!r}")
                            except Exception:
                                props_info.append(f"{p.name}=?")
                        log.info(f"  Cap FØR fiks {cap.protocol_id}: {', '.join(props_info)}")
                    except Exception:
                        log.info(f"  Cap FØR fiks {cap.protocol_id}: port={cap.port}")
            except Exception as e:
                log.warning(f"  Listing server capabilities feilet: {e}")

            # Fase 2: Fiks PrimaryConnectionString i ServerCapabilities.
            # Pi med to IP-adresser (WiFi + LAN) kan føre til at
            # NativeStreaming-serveren vel ein annan IP enn OPC-UA.
            # DewesoftX finn eininga via mDNS (WiFi-IP), les OPC-UA (WiFi-IP),
            # men NativeStreaming-cap kan peike til LAN-IP → AddDevice feilar.
            # Berre PrimaryConnectionString vert sett — Addresses/ConnectionStrings
            # er ListProperty som kan trigge DataType-åtvaringar.
            self._fiks_primary_connection_strings(ip)

            # Fase 3: Aktiver mDNS-discovery
            for srv_type, server in servers_added:
                try:
                    server.enable_discovery()
                    log.info(f"  {srv_type}: discovery aktivert")
                except Exception as e_disc:
                    log.warning(f"  {srv_type}: enable_discovery feilet: {e_disc}")

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
        """Oppdater serienummer/enhetsnamn etter oppstart.

        Lagrar verdiane for logging — endring av DeviceInfo via
        set_property_value er deaktivert fordi det kan krasje DewesoftX.
        """
        self._serienummer = serienummer or self._serienummer
        self._enhetsnamn = enhetsnamn or self._enhetsnamn
        log.info(f"  Enhetsinfo oppdatert (intern): sn={self._serienummer}")

    def _init_pakett_injeksjon(self):
        """Initialiser DataPacket-injeksjon for streaming av reelle SIRIUS-data.

        Hentar domain-signal-referansar og descriptors frå kvar kanal
        slik at oppdater_data() kan opprette DataPackets med ekte ADC-samples.

        Krev OPENDAQ_DISABLE_ACQ=1 i env for å hindre RefDevice si
        interne acqLoop frå å generere konfliktande syntetisk data.
        """
        self._dom_signal = []
        self._dom_desc = []
        self._val_desc = []
        self._total_samples = []
        self._pakett_klar = False

        # Sjekk om DataPacket API er tilgjengeleg i Python-bindingane
        if not hasattr(_daq, 'DataPacket') or not hasattr(_daq, 'DataPacketWithDomain'):
            log.warning("  DataPacket API ikkje tilgjengeleg — "
                        "kan ikkje injisere reelle data")
            return

        # Starttid: mikrosekund sidan epoch (matchar RefDevice-mønster)
        import time
        self._start_ticks = int(time.time() * 1_000_000)

        kanalar_klar = 0
        for idx, (ch, sig) in enumerate(self._kanal_signal):
            if sig is None:
                self._dom_signal.append(None)
                self._dom_desc.append(None)
                self._val_desc.append(None)
                self._total_samples.append(0)
                continue
            try:
                # Les domain_signal og descriptor via ISignal (read-only).
                # ISignalConfig har berre setter for desse — ikkje getter.
                _, sig_ro = self._kanal_signal_ro[idx]
                dom_sig_raw = sig_ro.domain_signal
                if dom_sig_raw is None:
                    raise ValueError("domain_signal er None")
                # Cast domain-signal til ISignalConfig for send_packet()
                try:
                    dom_sig = _daq.ISignalConfig.cast_from(dom_sig_raw)
                except Exception:
                    dom_sig = dom_sig_raw
                dom_desc = sig_ro.domain_signal.descriptor
                val_desc = sig_ro.descriptor
                if dom_desc is None or val_desc is None:
                    raise ValueError("descriptor er None")

                self._dom_signal.append(dom_sig)
                self._dom_desc.append(dom_desc)
                self._val_desc.append(val_desc)
                self._total_samples.append(0)
                kanalar_klar += 1

                # Hent tick delta frå domain descriptor sin data rule
                try:
                    rule = dom_desc.rule
                    params = rule.parameters
                    if hasattr(params, 'get'):
                        delta = params.get('delta', self._tick_delta)
                    elif hasattr(params, '__getitem__'):
                        delta = params['delta']
                    else:
                        delta = self._tick_delta
                    self._tick_delta = int(delta)
                except Exception:
                    pass  # Behald standard tick_delta

            except Exception as e:
                ch_name = ch.name if hasattr(ch, 'name') else f"ch{idx}"
                log.warning(f"  Kanal {ch_name}: pakett-init feilet: {e}")
                self._dom_signal.append(None)
                self._dom_desc.append(None)
                self._val_desc.append(None)
                self._total_samples.append(0)

        if kanalar_klar > 0:
            self._pakett_klar = True
            log.info(f"  Pakett-injeksjon klar: {kanalar_klar} kanalar, "
                     f"delta={self._tick_delta} ticks/sample")
            if not os.environ.get("OPENDAQ_DISABLE_ACQ"):
                log.warning("  ADVARSEL: OPENDAQ_DISABLE_ACQ ikkje sett! "
                            "RefDevice genererer OGSAA syntetisk data!")
        else:
            log.warning("  Pakett-injeksjon IKKJE klar — ingen domain-signal")

    def oppdater_data(self, kanal_data):
        """
        Injiser ADC-data frå SIRIUS inn i openDAQ-signalar.

        Opprettar DataPacket med reelle maaleverdiar og sender dei
        direkte til openDAQ-signalane via send_packet(). DewesoftX
        mottek verdiane via NativeStreaming/OPC-UA.

        Krev OPENDAQ_DISABLE_ACQ=1 for å unngå konflikt med
        RefDevice si interne datagenering.

        Args:
            kanal_data: dict {"kanal_0": np.array(int16), ...} fraa SiriusDriver
        """
        if not self._tilgjengelig or not self._kanal_signal:
            return

        import time
        self._sirius_aktiv = True
        self._sirius_ts = time.time()

        # Eingongs-diagnostikk: logg fyrste gong data kjem inn
        if self._data_teller == 0:
            log.info(f"  oppdater_data: fyrste kall, kanalar={list(kanal_data.keys())}, "
                     f"signal_count={len(self._kanal_signal)}, "
                     f"pakett_klar={self._pakett_klar}")

        # Berekn statistikk berre kvar 20. pakke (~1 Hz ved 20 pkt/sek)
        berekn_stats = (self._data_teller % 20 == 0)

        try:
            for kanal_idx, key in enumerate(self._KANAL_KEYS):
                if kanal_idx >= len(self._kanal_signal):
                    break

                data = kanal_data.get(key)
                if data is None or len(data) == 0:
                    continue

                ch, sig = self._kanal_signal[kanal_idx]
                if sig is None:
                    continue

                # Skaler int16 ADC-verdiar til fysiske einingar (V/A)
                skala = (self._kanal_skala[kanal_idx]
                         if kanal_idx < len(self._kanal_skala) else 1.0)
                offset = (self._kanal_offset[kanal_idx]
                          if kanal_idx < len(self._kanal_offset) else 0.0)
                # Gjenbruk pre-allokert buffer viss storleiken matchar
                n = len(data)
                if n <= len(self._fdata_buf):
                    fdata = self._fdata_buf[:n]
                    np.multiply(data, skala, out=fdata, casting='unsafe')
                else:
                    fdata = data.astype(np.float64) * skala
                if offset != 0.0:
                    fdata += offset

                # Berekn statistikk for web UI (kvar 20. pakke = ~1 Hz)
                if berekn_stats:
                    snitt = float(np.mean(fdata))
                    rms = float(np.sqrt(np.mean(fdata ** 2)))
                    topp = float(np.max(np.abs(fdata)))

                    self._siste_verdiar[key] = {
                        "snitt": round(snitt, 4),
                        "rms": round(rms, 4),
                        "topp": round(topp, 4),
                        "siste": round(float(data[-1]) * skala + offset, 4),
                        "antall": len(data),
                        "kjelde": "sirius",
                    }

                # Send reelle data som DataPacket via openDAQ-signal
                if (self._pakett_klar and
                        kanal_idx < len(self._dom_signal) and
                        self._dom_signal[kanal_idx] is not None):
                    try:
                        dom_sig = self._dom_signal[kanal_idx]
                        dom_desc = self._dom_desc[kanal_idx]
                        val_desc = self._val_desc[kanal_idx]

                        num_samples = len(fdata)
                        offset = (self._start_ticks +
                                  self._total_samples[kanal_idx] * self._tick_delta)

                        # Opprett domain- (tid) og verdi-pakke
                        time_pkt = _daq.DataPacket(dom_desc, num_samples, offset)
                        val_pkt = _daq.DataPacketWithDomain(
                            time_pkt, val_desc, num_samples, 0)

                        # Fyll verdibufferen med reelle SIRIUS-data
                        try:
                            buf = np.frombuffer(
                                val_pkt.raw_data, dtype=np.float64)
                            np.copyto(buf, fdata[:num_samples])
                        except (ValueError, TypeError):
                            # Fallback: ctypes for ikkje-skrivbar buffer
                            import ctypes
                            arr = (ctypes.c_double * num_samples).from_address(
                                int(val_pkt.raw_data))
                            for i in range(min(num_samples, len(fdata))):
                                arr[i] = fdata[i]

                        # Send: domain fyrst, deretter verdi
                        dom_sig.send_packet(time_pkt)
                        sig.send_packet(val_pkt)

                        self._total_samples[kanal_idx] += num_samples
                    except Exception as e:
                        if self._data_teller % 1000 == 0:
                            log.warning(f"  Pakett kanal {kanal_idx}: {e}")

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
                # Generer simulerte verdiar frå SUNDET_KANALAR (berre ADC-kanalar)
                t = time.time() - t0
                for i, cfg in enumerate(self.SUNDET_KANALAR):
                    if i >= 8:  # Berre 8 ADC-kanalar
                        break
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
        """Stopp openDAQ instance og servere.

        Eksplisitt opprydding av servere og instance for å frigjere
        portar (4840, 7420, 7414) slik at restart kan binde dei på nytt.

        VIKTIG: Referansar MÅ fjernast i riktig rekkefølge:
        1. Stopp leser-tråden (held referanse til self via metode-binding)
        2. Fjern serverar eksplisitt (frigjer nettverks-lyttarar)
        3. Fjern kanal/signal-referansar (held C++ channel/signal-objekt)
        4. Fjern _device FYRST (same objekt som _instance)
        5. Fjern _instance SIST
        6. GC for å trigge C++-destruktorar som frigjer OS-sockets
        """
        log.info("openDAQ nettverksbro stoppar...")
        self._tilgjengelig = False
        self._stopp_event.set()
        if self._leser_traad and self._leser_traad.is_alive():
            self._leser_traad.join(timeout=3)
        with self._lock:
            self._status["aktiv"] = False

        # Fjern serverane eksplisitt FØR instance-destruksjon.
        # remove_server() stoppar nettverks-lyttarar og frigjer portar.
        if self._instance is not None:
            try:
                servers = list(self._instance.servers)
                for idx, srv in enumerate(servers):
                    try:
                        self._instance.remove_server(srv)
                        log.info(f"  Server {idx} fjerna")
                    except Exception as e:
                        log.warning(f"  Server {idx} fjerning feilet: {e}")
                log.info(f"  {len(servers)} serverar prosessert")
            except Exception as e:
                log.warning(f"  Listing/fjerning av serverar feilet: {e}")

        # Nullstill ALLE referansar som held C++-objekt i live.
        # Rekkefølge er kritisk: barnereferansar fyrst, deretter foreldre.
        self._kanal_signal = []   # Kanal/signal-referansar
        self._kanal_skala = []
        self._kanal_offset = []
        self._siste_verdiar = {}
        self._dom_signal = []     # DataPacket-injeksjon referansar
        self._dom_desc = []
        self._val_desc = []
        self._total_samples = []
        self._pakett_klar = False
        self._device = None       # Same objekt som _instance — fjern fyrst
        inst = self._instance     # Lokal referanse for eksplisitt del
        self._instance = None     # Fjern instansreferanse

        # Eksplisitt del + dobbel GC for å tvinge C++-destruktorar.
        # Python GC kan trenge fleire pass for finalizer-kjeder.
        try:
            del inst
        except Exception:
            pass
        import gc
        gc.collect()
        gc.collect()
        log.info("openDAQ nettverksbro stoppa")

    # Fast kanal-nøklar for hot loop (unngår sorted() og list-oppretting per kall)
    _KANAL_KEYS = [f"kanal_{i}" for i in range(8)]

    # Kanalopsett (SIRIUSi-HS, 4×Hi-LV + 4×Lo-LV)
    # AI 1-3: Hi-LV, spenning 230V RMS (325V topp), 50 Hz
    # AI 4:   Hi-LV, ikkje tilkobla
    # AI 5-7: Lo-LV, integrator 0-3V → 0-6000A (5V-driven)
    #         Lo-LV ADC ±5V, sensor-skalering gjer resten
    # AI 8:   Lo-LV, ikkje tilkobla
    SUNDET_KANALAR = [
        {"namn": "AI 0", "amplitude": 325.0, "freq": 50.0, "range": (-1600, 1600)},
        {"namn": "AI 1", "amplitude": 325.0, "freq": 50.0, "range": (-1600, 1600)},
        {"namn": "AI 2", "amplitude": 325.0, "freq": 50.0, "range": (-1600, 1600)},
        {"namn": "AI 3", "amplitude": 0.0,   "freq": 50.0, "range": (-1600, 1600)},
        {"namn": "AI 4", "amplitude": 100.0, "freq": 50.0, "range": (-5, 5)},
        {"namn": "AI 5", "amplitude": 100.0, "freq": 50.0, "range": (-5, 5)},
        {"namn": "AI 6", "amplitude": 100.0, "freq": 50.0, "range": (-5, 5)},
        {"namn": "AI 7", "amplitude": 0.0,   "freq": 50.0, "range": (-5, 5)},
        {"namn": "Tid",  "amplitude": 0.0,   "freq": 1.0,  "range": (0, 3600)},
    ]

    def _konfig_kanalar(self):
        """Sett 9 kanalar med namn og eigenskapar frå kanal_konfig (eller fallback til SUNDET).

        Kanal 0-7: ADC-kanalar (Sine waveform, data frå SIRIUS)
        Kanal 8:   Tidskanal (Counter waveform, value = globalSampleIndex)

        openDAQ referanse-eininga har faste grenser for eigenskapar:
          - Amplitude: Float, [0, 10]
          - Frequency: Float, [0.1, 10000]
          - DC:        Float, [-10, 10]
          - Waveform:  Int (Selection), 0=Sine, 3=Counter
          - Offset:    Int, min=0 (sample-offset, ikkje DC)
          - GlobalSampleRate: Float, [1, 1000000]
        Verdiar vert klampa automatisk av _safe_set().
        """
        # Berre 8 ADC-kanalar (ikkje 9).
        # Counter waveform (Waveform=3) for tidskanal manglar domain-signal,
        # som krasjar DewesoftX i TryGetSampleRate med "Interface object is nil".
        if not self._safe_set(self._device, "NumberOfChannels", 8):
            log.warning("  Kunne ikkje sette NumberOfChannels — avbryt kanalkonfig")
            return
        log.info("  NumberOfChannels sett til 8 (utan tidskanal)")

        # GlobalSampleRate er FloatProperty — MÅ vere float, ikkje int!
        # Les frå SAMPLE_RATE env var (default 20000 = SIRIUS hardware rate)
        sample_rate = float(os.environ.get("SAMPLE_RATE", "20000"))
        if self._safe_set(self._device, "GlobalSampleRate", sample_rate):
            log.info(f"  GlobalSampleRate sett til {sample_rate} Hz")

        # Logg device-eigenskapar for feilsøking
        self._logg_eigenskapar(self._device, "Dev.")

        # Les persistert konfig (fallback til standard)
        kanal_konfig = les_konfig()

        # Bygg skaleringsfaktorar: SIRIUS ADC leverer int16 (-32768..32767)
        # som representerer full skala av input-rangen.
        # Utan sensor: physical_value = raw_int16 * (range_max / 32768)
        # Med sensor:  physical_value = raw_int16 * (amp_range / 32768) * slope + offset
        self._kanal_skala = []
        self._kanal_offset = []

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

                # Hopp over tidskanalen (indeks 8) — ikkje brukt med 8 kanalar
                if kk.type == "generic" and kk.namn == "Tid":
                    continue
                if i >= 8:
                    break
                # Amplitude er FloatProperty [0, 10] — _safe_set klampar
                self._safe_set(ch, "Amplitude", amp if kk.aktiv else 0.0)
                # Frequency er FloatProperty [0.1, 10000]
                self._safe_set(ch, "Frequency", freq)
                # DC er FloatProperty [-10, 10]
                self._safe_set(ch, "DC", 0.0)
                # Waveform er SelectionProperty (int): 0=Sine
                self._safe_set(ch, "Waveform", 0)

                # Berekn skalering og CustomRange (med sensor-støtte)
                if kk.sensor_aktiv and (kk.sensor_inn_2 != kk.sensor_inn_1):
                    # To-punkt sensor: voltage → physical
                    # voltage = raw_int16 × (amp_range / 32768)
                    # physical = (voltage - inn_1) × slope + ut_1
                    amp_range = kk.range_max  # Forsterkar-range (t.d. 5V)
                    slope = ((kk.sensor_ut_2 - kk.sensor_ut_1) /
                             (kk.sensor_inn_2 - kk.sensor_inn_1))
                    offset = kk.sensor_ut_1 - slope * kk.sensor_inn_1
                    skala = amp_range / 32768.0 * slope
                    self._kanal_skala.append(skala)
                    self._kanal_offset.append(offset)

                    # Display-range frå sensor-skalering
                    display_min = kk.range_min * slope + offset
                    display_max = kk.range_max * slope + offset
                    try:
                        custom_range = _daq.Range(display_min, display_max)
                        ch.set_property_value("CustomRange", custom_range)
                        log.info(f"  {kk.namn}: CustomRange=[{display_min}, {display_max}] "
                                 f"(sensor: {kk.sensor_namn}, slope={slope}, offset={offset})")
                    except Exception as e_cr:
                        log.warning(f"  {kk.namn}: CustomRange feilet: {e_cr}")
                    log.info(f"  {kk.namn}: aktiv={kk.aktiv}, sensor={kk.sensor_namn}, "
                             f"skala={skala:.6f}, offset={offset}")
                else:
                    # Direkte skalering utan sensor
                    skala = kk.range_max / 32768.0 if kk.range_max > 0 else 1.0
                    self._kanal_skala.append(skala)
                    self._kanal_offset.append(0.0)

                    try:
                        custom_range = _daq.Range(float(kk.range_min), float(kk.range_max))
                        ch.set_property_value("CustomRange", custom_range)
                        log.info(f"  {kk.namn}: CustomRange=[{kk.range_min}, {kk.range_max}]")
                    except Exception as e_cr:
                        log.warning(f"  {kk.namn}: CustomRange feilet: {e_cr}")
                    log.info(f"  {kk.namn}: aktiv={kk.aktiv}, "
                             f"range=[{kk.range_min}, {kk.range_max}], type={kk.type}")
            except Exception as e:
                log.warning(f"  Kanal {i} konfig feilet: {e}")

        # Logg alle kanalar sine eigenskapar for å finne nil-verdiar
        for idx, ch in enumerate(channels):
            if idx < len(kanal_konfig):
                self._logg_eigenskapar(ch, f"Ch{idx}.")

    def _legg_til_sample_rate_eigenskap(self):
        """Legg til GetPossibleSampleRate på device og kanalar.

        DewesoftX sin TDSOpenDaqAI.CalcADCSampleRate les denne eigenskapen
        for å bestemme tilgjengelege sample rates. Utan den feilar det med
        openDAQ Error 0x80000006 og alle kanalar viser "Invalid or no data".

        SIRIUSi-HS maks sample rate: 200 kHz.
        Eigenskapnamnet er singularis ("Rate" ikkje "Rates") — truleg ein
        enkeltverdi (maks mogleg rate), ikkje ei liste.

        ListProperty og IntProperty ga begge 0x80004002 (E_NOINTERFACE).
        Prøver FloatProperty (same type som GlobalSampleRate).
        """
        max_rate = 200000.0  # SIRIUSi-HS maks: 200 kHz

        targets = [("Device", self._device)]
        try:
            for i, ch in enumerate(self._device.channels):
                targets.append((f"Ch{i}", ch))
        except Exception:
            pass

        for label, obj in targets:
            try:
                # Sjekk om eigenskapen allereie finst
                try:
                    obj.get_property("GetPossibleSampleRate")
                    log.info(f"  {label}: GetPossibleSampleRate finst allereie")
                    continue
                except Exception:
                    pass

                prop = _daq.FloatPropertyBuilder("GetPossibleSampleRate", max_rate)
                obj.add_property(prop.build())
                log.info(f"  {label}: GetPossibleSampleRate = {max_rate} Hz")
            except Exception as e:
                log.warning(f"  {label}: GetPossibleSampleRate feilet: {e}")

    def _legg_til_dewesoftx_eigenskapar(self):
        """Legg til dummy-eigenskapar som DewesoftX si OpenDAQSettingsFrame forventer.

        DewesoftX krasjar med Access Violation i TOpenDAQFrame.Initialize (linje 482)
        når den prøver å lese eigenskapar som ikkje finst på RefDevice:
          - DeviceLogLevel: SelectionProperty for log level combo box
          - Ymse andre konfig-eigenskapar settings-panelet treng

        Stakksporet:
          DeviceLogLevelItemComboSelect → RebootDeviceItemButtonClick
          → Reconnect → SettingsChanged → Initialize → KRASJ (null deref)

        Ved å legge til desse eigenskapane unngår vi null pointer i DewesoftX.
        """
        # DeviceLogLevel: Selection med vanlege log levels
        # DewesoftX sin DeviceLogLevelItemComboSelect les denne for å populere dropdown
        eigenskapar = [
            ("DeviceLogLevel", "int", 2),      # 0=Trace,1=Debug,2=Info,3=Warn,4=Error
            ("DeviceLogPath", "string", ""),    # Sti til loggfil (DewesoftX kan spørje)
        ]

        for namn, typ, default in eigenskapar:
            try:
                # Sjekk om eigenskapen allereie finst
                try:
                    self._device.get_property(namn)
                    log.info(f"  DewesoftX-prop {namn}: finst allereie")
                    continue
                except Exception:
                    pass

                if typ == "int":
                    prop = _daq.IntPropertyBuilder(namn, int(default))
                    self._device.add_property(prop.build())
                elif typ == "string":
                    prop = _daq.StringPropertyBuilder(namn, str(default))
                    self._device.add_property(prop.build())
                elif typ == "float":
                    prop = _daq.FloatPropertyBuilder(namn, float(default))
                    self._device.add_property(prop.build())

                log.info(f"  DewesoftX-prop {namn} = {default!r}")
            except Exception as e:
                log.warning(f"  DewesoftX-prop {namn} feilet: {e}")

    # Amplifier-namn per kanal (matchar DewesoftX direkte USB-visning)
    _AMPL_NAMN = {
        "voltage": "SIRIUS-HS-HVv2",   # Hi-LV forsterkarkort
        "current": "SIRIUS-HS-LVv2",   # Lo-LV forsterkarkort
        "generic": "SIRIUS-HS",
    }

    def _sett_signal_descriptors(self):
        """Sett signal descriptors med einingar, range og metadata per kanal.

        DewesoftX les signal descriptors for å vise:
          - Units (V/A) i kolonnen "Units"
          - Physical quantity i kolonnen "Physical quantity"
          - Measurement type i kolonnen "Measurement"
          - Amplifier name i kolonnen "Ampl. name"

        Opprettar DataDescriptorBuilder med UnitBuilder per kanal basert på
        kanal_konfig (type, enhet, range). Sett ny descriptor via
        ISignalConfig.set_descriptor() + legg til AmplifierName som
        StringProperty per kanal.
        """
        kanal_konfig = les_konfig()

        # Sjekk at nødvendige API-ar finst
        for attr in ('DataDescriptorBuilder', 'UnitBuilder', 'SampleType'):
            if not hasattr(_daq, attr):
                log.warning(f"  Signal descriptors: {attr} ikkje tilgjengeleg "
                            f"— kan ikkje sette einingar")
                return

        sett_teller = 0
        for idx, (ch, sig) in enumerate(self._kanal_signal):
            if sig is None or idx >= len(kanal_konfig):
                continue
            kk = kanal_konfig[idx]

            try:
                # 1. Bygg einingsobjekt (Unit)
                # Bruk sensor-eining viss sensor er aktiv
                unit_builder = _daq.UnitBuilder()
                if kk.sensor_aktiv and kk.sensor_enhet:
                    enhet = kk.sensor_enhet
                    if enhet == "A":
                        unit_builder.name = "ampere"
                        unit_builder.symbol = "A"
                        unit_builder.quantity = "electric_current"
                    else:
                        unit_builder.name = enhet
                        unit_builder.symbol = enhet
                        unit_builder.quantity = kk.type or ""
                elif kk.type == "voltage":
                    unit_builder.name = "volt"
                    unit_builder.symbol = "V"
                    unit_builder.quantity = "voltage"
                elif kk.type == "current":
                    unit_builder.name = "ampere"
                    unit_builder.symbol = "A"
                    unit_builder.quantity = "electric_current"
                else:
                    unit_builder.name = kk.enhet or ""
                    unit_builder.symbol = kk.enhet or ""
                    unit_builder.quantity = kk.type or ""
                unit_obj = unit_builder.build()

                # 2. Bygg descriptor med eining og range
                desc_builder = _daq.DataDescriptorBuilder()
                desc_builder.name = kk.namn
                desc_builder.sample_type = _daq.SampleType.Float64
                desc_builder.unit = unit_obj

                # value_range: fysisk range (berekna frå sensor viss aktiv)
                try:
                    if kk.sensor_aktiv and (kk.sensor_inn_2 != kk.sensor_inn_1):
                        slope = ((kk.sensor_ut_2 - kk.sensor_ut_1) /
                                 (kk.sensor_inn_2 - kk.sensor_inn_1))
                        s_offset = kk.sensor_ut_1 - slope * kk.sensor_inn_1
                        display_min = kk.range_min * slope + s_offset
                        display_max = kk.range_max * slope + s_offset
                        desc_builder.value_range = _daq.Range(display_min, display_max)
                    else:
                        desc_builder.value_range = _daq.Range(
                            float(kk.range_min), float(kk.range_max))
                except Exception:
                    pass  # Ikkje alle SDK-versjonar støttar value_range

                new_desc = desc_builder.build()

                # 3. Sett descriptor på signal via ISignalConfig
                try:
                    sig.set_descriptor(new_desc)
                except Exception as e_desc:
                    # Fallback: prøv descriptor eigenskap direkte
                    try:
                        sig.descriptor = new_desc
                    except Exception:
                        log.warning(f"  {kk.namn}: set_descriptor feilet: {e_desc}")
                        continue

                sett_teller += 1
                log.info(f"  {kk.namn}: descriptor unit={kk.enhet}, "
                         f"quantity={unit_builder.quantity}, "
                         f"range=[{kk.range_min}, {kk.range_max}]")

                # 4. Oppdater lagra val_desc for DataPacket-injeksjon
                if idx < len(self._val_desc):
                    self._val_desc[idx] = new_desc

            except Exception as e:
                log.warning(f"  {kk.namn}: signal descriptor feilet: {e}")

            # 5. Legg til AmplifierName som kanal-eigenskap
            ampl_namn = self._AMPL_NAMN.get(kk.type, "SIRIUS-HS")
            try:
                try:
                    ch.get_property("AmplifierName")
                except Exception:
                    prop = _daq.StringPropertyBuilder("AmplifierName", ampl_namn)
                    ch.add_property(prop.build())
                    log.info(f"  {kk.namn}: AmplifierName={ampl_namn}")
            except Exception as e:
                log.warning(f"  {kk.namn}: AmplifierName feilet: {e}")

        log.info(f"  Signal descriptors sett: {sett_teller}/{len(self._kanal_signal)} kanalar")

    def _sett_sirius_device_info(self):
        """Sett DeviceInfo til å matche ekte Dewesoft SIRIUS.

        DewesoftX identifiserer einingar basert på DeviceInfo-felt.
        Standard referanse-eining rapporterer 'RefDev0' / 'openDAQ' som
        DewesoftX ikkje kjenner att. Sett til SIRIUS-verdiar slik at
        DewesoftX handterer eininga som ein kjent Dewesoft-enhet.
        """
        sirius_info = {
            "name": self._enhetsnamn or "SIRIUSi-HS",
            "manufacturer": "Dewesoft",
            "model": "SIRIUSi-HS",
            "serialNumber": self._serienummer or "0000000000",
        }
        try:
            info = self._device.info
            for prop_name, value in sirius_info.items():
                try:
                    info.set_property_value(prop_name, value)
                    log.info(f"  DeviceInfo.{prop_name} = {value}")
                except Exception as e:
                    # Prøv alternativ: direkte attributt-setting
                    try:
                        py_attr = prop_name
                        if prop_name == "serialNumber":
                            py_attr = "serial_number"
                        setattr(info, py_attr, value)
                        log.info(f"  DeviceInfo.{prop_name} = {value} (via attributt)")
                    except Exception as e2:
                        log.info(f"  DeviceInfo.{prop_name}: ikkje endrbar ({e})")
        except Exception as e:
            log.warning(f"  Sett SIRIUS DeviceInfo feilet: {e}")

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

    def _fiks_primary_connection_strings(self, ip):
        """Sett PrimaryConnectionString på alle ServerCapabilities til riktig IP.

        Når Pi har to nettverksinterface (t.d. WiFi + LAN) kan openDAQ sine
        serverar velje ulike IP-adresser. DewesoftX finn eininga via mDNS
        (éin IP) men NativeStreaming-cap kan peike til ein annan IP.

        BERRE PrimaryConnectionString vert sett — Addresses og ConnectionStrings
        er ListProperty<IString> som kan trigge OPC-UA "DataType incompatible"-
        åtvaringar i v3.20.6. DewesoftX brukar PrimaryConnectionString.
        """
        try:
            caps = self._instance.info.server_capabilities
            for cap in caps:
                proto_id = cap.protocol_id
                prefix = cap.prefix
                port = cap.port

                # Hopp over NativeConfiguration (daq.nd://) — protokollen
                # feiler mellom ulike openDAQ-versjonar (3.30.0 klient →
                # 3.20.6 server). La PrimaryConnectionString stå tom slik
                # at klientar ikkje prøver daq.nd:// og fell tilbake til
                # daq.opcua:// + daq.ns:// som begge fungerer.
                if proto_id == "OpenDAQNativeConfiguration":
                    log.info(f"  Cap {proto_id}: HOPPA OVER (daq.nd:// deaktivert)")
                    continue

                ny_conn = f"{prefix}://{ip}:{port}/"

                # Les noverande verdi for å sjekke om den allereie er riktig
                try:
                    noverande = cap.get_property_value("PrimaryConnectionString")
                except Exception:
                    noverande = ""

                if ip in str(noverande):
                    log.info(f"  Cap {proto_id}: PrimaryConnectionString OK ({noverande})")
                    continue

                if self._safe_set(cap, "PrimaryConnectionString", ny_conn):
                    log.info(f"  Cap {proto_id}: PrimaryConnectionString {noverande!r} → {ny_conn}")
                else:
                    log.warning(f"  Cap {proto_id}: Kunne ikkje sette PrimaryConnectionString")
        except Exception as e:
            log.warning(f"  _fiks_primary_connection_strings feilet: {e}")

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

    def _fiks_nil_strings(self, obj, label=""):
        """Sett nil string-eigenskapar til tom streng.

        DewesoftX krasjar med 'Interface object is nil' i InitStringProperty
        når ein string-eigenskap har nil-verdi (nullptr) i staden for "".
        Denne metoden itererer alle synlege eigenskapar og fiksar nil-strings.
        """
        ct = getattr(_daq, 'CoreType', None)
        if ct is None:
            return
        try:
            for prop in obj.visible_properties:
                try:
                    if prop.value_type == ct.ctString:
                        try:
                            val = obj.get_property_value(prop.name)
                        except Exception:
                            val = None  # get_property_value kan kaste for nil
                        if val is None:
                            try:
                                obj.set_property_value(prop.name, "")
                                log.info(f"  {label}{prop.name}: nil → ''")
                            except Exception:
                                pass  # Read-only property
                    else:
                        # Logg nil-verdiar av andre typar (objekt, liste, etc.)
                        try:
                            val = obj.get_property_value(prop.name)
                            if val is None:
                                log.warning(f"  {label}{prop.name}: nil "
                                            f"(type={prop.value_type})")
                        except Exception:
                            log.warning(f"  {label}{prop.name}: kan ikkje "
                                        f"lesast (type={prop.value_type})")
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"  _fiks_nil_strings({label}): {e}")

    def _fiks_alle_nil_strings(self):
        """Fiks nil string-eigenskapar på device, device info og alle kanalar."""
        # Device-eigenskapar (med set_root_device er instance = device)
        self._fiks_nil_strings(self._device, "Dev.")

        # DeviceInfo-eigenskapar
        try:
            info = self._device.info
            if info:
                self._fiks_nil_strings(info, "Info.")
        except Exception as e:
            log.warning(f"  DeviceInfo nil-fiks: {e}")

        # Kanal-eigenskapar
        try:
            for i, ch in enumerate(self._device.channels):
                self._fiks_nil_strings(ch, f"Ch{i}.")
        except Exception as e:
            log.warning(f"  Kanal nil-fiks: {e}")

        log.info("  Nil string-eigenskapar fiksa")

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
        """Finn maskinens IP-adresse.

        Brukar OPENDAQ_IP env var fyrst (sett i docker-entrypoint.sh),
        deretter fallback til socket-deteksjon.
        """
        env_ip = os.environ.get("OPENDAQ_IP", "").strip()
        if env_ip:
            return env_ip
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
