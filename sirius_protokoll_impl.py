#!/usr/bin/env python3
"""
SIRIUS USB-protokoll implementasjon (Lag 1)
=============================================
Lavnivaa protokoll-lag basert paa reverse-engineering av DewesoftX USB-trafikk
fanget med usbmon paa Raspberry Pi.

Protokollen bruker fleire kommandotyper:

  Enkle kommandoer (send + les svar, ingen B1-poll):
    0x00: Firmware-versjon
    0xA0: Sett modus (aktiv/inaktiv)
    0xA1: Hent slot-tilstedevaerelse
    0xAC: Hent slot-typer
    0xA8: Les EEPROM (enhetsinfo, serienummer, kalibrering)
    0xAE: Telemetri/heartbeat
    0xB0: Initialiser/reset

  AD-kommandoer (send AD + les ACK + send B1 poll + les resultat):
    0xAD med op=0x08: Slot-query (global)
    0xAD med op=0x0C: Slot-enumerering
    0xAD med op=0x13: Register-skriving
    0xAD med op=0x14: Register-lesing
    0xAD med op=0x1C: Batch-operasjon

  Register 0xA5 er eit kommando-dispatch-register med sub-kommandoer:
    [01 param 5A] = SET_PARAM (5A er skrivelaasnoekkelen)
    [02 reg val]  = SET_MODE
    [03 reg off]  = READ_STRING
    [06 reg val]  = SET_CONFIG

Datastroemming:
    EP2 IN: ADC-data (32 bytes = 2 rammer x 8 kanaler x int16 LE)
    EP4 IN: Kontrollstatus (20 bytes, normalt nuller)
    EP6 IN: Synkronisering (32 bytes = 4 x 8 bytes med 0xE0-markering)
"""

import struct
import threading
import logging

log = logging.getLogger('sirius_protokoll')

# --- USB-identifikatorer ---
DEWESOFT_VID = 0x1CED
SIRIUS_PID = 0x1002

# --- Endepunktadresser ---
EP_CMD_OUT = 0x01   # Kommandoer til enhet (bulk)
EP_CMD_IN = 0x81    # Svar fra enhet (bulk)
EP_ADC_IN = 0x82    # ADC-datastroemming (bulk)
EP_CTRL_IN = 0x84   # Kontrolldata (bulk)
EP_SYNC_IN = 0x86   # Synk/tidsstempel (bulk)
EP_DATA_OUT = 0x08  # Data til enhet (bulk)

# --- Enkle kommando-opkoder ---
OPCODE_FW_VERSJON = 0x00    # Firmware-versjon
OPCODE_SETMODE = 0xA0       # Sett modus
OPCODE_GETCONFIG = 0xA1     # Hent slot-tilstedevaerelse
OPCODE_PRESTART = 0xA4      # Pre-start modus (sendes foer start-sekvens)
OPCODE_EEPROM = 0xA8        # Les EEPROM
OPCODE_GETSLOTTYPES = 0xAC  # Hent slot-typer
OPCODE_TELEMETRI = 0xAE     # Telemetri/heartbeat
OPCODE_INIT = 0xB0          # Initialiser/reset

# --- AD-kommando ---
OPCODE_AD = 0xAD       # Register les/skriv
OPCODE_POLL = 0xB1     # Poll for svar

# --- Operasjonstyper for 0xAD ---
OP_SLOT_QUERY = 0x08  # Global slot-query
OP_SLOT_ENUM = 0x0C   # Slot-enumerering
OP_SKRIV = 0x13       # Skriv til register
OP_LES = 0x14         # Les fra register
OP_BATCH = 0x1C       # Batch-operasjon

# --- Slotter (analoge inngangsmoduler) ---
SLOT_0 = 0x04
SLOT_1 = 0x05
SLOT_2 = 0x06
SLOT_3 = 0x07
ALLE_SLOTTER = [SLOT_0, SLOT_1, SLOT_2, SLOT_3]

# --- Register i A5 kommando-dispatch ---
REG_CMD = 0xA5        # Kommando-dispatch-register
REG_COMMIT = 0x5A     # Commit/trigger-register
REG_TRIG_33 = 0x33    # Lese-trigger A
REG_TRIG_08 = 0x08    # Lese-trigger B
REG_TRIG_0B = 0x0B    # Lese-trigger C (modulstrenger)
REG_TRIG_0A = 0x0A    # Lese-trigger D (binaerdata)
REG_SAMPLE_CFG = 0xCC # Samplingsoppsett

# --- A5 sub-kommandoer ---
A5_SET_PARAM = 0x01     # Sett parameter
A5_SET_MODE = 0x02      # Sett modus
A5_READ_STRING = 0x03   # Les streng/data
A5_SET_CONFIG = 0x06    # Utvidet konfigurasjon

# --- Excitation (forsterkar-spenning) register ---
# Fraa DewesoftX pcapng-analyse: Lo-LV slotter brukar desse for integrator-spenning
REG_EXC_ENABLE = 0xC3   # Excitation ON:  A5 02 C3 01
REG_EXC_VOLT = 0xBC     # Voltage level:  A5 02 BC 04 (04 = 5V unipolar)
REG_EXC_OFF = 0xC9      # Excitation OFF: A5 02 C9 00

# --- Magiske verdiar ---
WRITE_KEY = 0x5A                        # Skrivelaasnoekkelen
TRIGGER_DATA = bytes([0x5A, 0x00, 0x00])  # Trigger-verdi for lese-registre
COMMIT_DATA = bytes([0x00, 0x00, 0x00])   # Commit-verdi

# --- Eldre opkoder (FX2-lag, beholdt for kompatibilitet) ---
OPCODE_IDENT = 0xE3    # Enhetsidentifikasjon (FX2)
OPCODE_DEVTYPE = 0xE4  # Enhetstype (FX2)

# Gamle namn (alias for bakoverkompatibilitet)
REG_SLOT_INFO = REG_CMD
REG_ADC_KONFIG = REG_TRIG_08
REG_KALIBRERING = REG_SAMPLE_CFG

# --- Timeouts (ms) ---
TIMEOUT_CMD = 1000    # Kommando send/motta
TIMEOUT_POLL = 2000   # Poll-venting
TIMEOUT_DATA = 5000   # Datastroemming
TIMEOUT_SHORT = 300   # Korte operasjoner

# --- Poll-konstanter ---
MAKS_POLL_FORSOK = 50    # Auka fraa 10 - globale register treng fleire forsok
POLL_KLAR = 1   # Nye data tilgjengelig (lesing)
POLL_VENT = 0   # Venter / skrivebekreftelse

# ACK-moenstre
ACK_BYTE = 0xFF

# --- ADC-format (fraa pcapng-analyse 2026-02-14) ---
ADC_KANALER = 8              # 8 kanalar per ramme
ADC_RAMMER_PER_PAKKE = 992   # 992 rammer per USB-pakke
ADC_PAKKESTORRELSE = 15872   # 992 * 8 * 2 bytes per EP2-pakke
# Eldre alias
ADC_SAMPLES_PER_PAKKE = ADC_RAMMER_PER_PAKKE


# --- Unntak ---

class SiriusFeil(Exception):
    """Generell SIRIUS-feil."""
    pass


class SiriusUSBFeil(SiriusFeil):
    """USB-kommunikasjonsfeil."""
    pass


class SiriusPollTimeout(SiriusFeil):
    """Timeout under polling - enheten svarte ikke i tide."""
    pass


class SiriusIkkeFunnet(SiriusFeil):
    """SIRIUS-enheten ble ikke funnet paa USB."""
    pass


class SiriusProtokoll:
    """
    Lavnivaa protokoll for kommunikasjon med Dewesoft SIRIUS over USB.

    Traadsikker paa kommandokanalen (EP1) via intern Lock.
    ADC-lesing (EP2) og sync-lesing (EP6) er uavhengige.
    """

    def __init__(self, dev):
        self._dev = dev
        self._cmd_lock = threading.Lock()
        self._lukket = False
        log.debug("SiriusProtokoll initialisert")

    @property
    def enhet(self):
        return self._dev

    # ---- Raa USB I/O ----

    def _send_cmd(self, data):
        """Send data paa EP1 OUT."""
        try:
            skrevet = self._dev.write(EP_CMD_OUT, data, timeout=TIMEOUT_CMD)
            log.debug(f"EP1 OUT [{len(data)}B]: {data.hex()}")
            return skrevet
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved sending paa EP1 OUT: {e}") from e

    def _les_svar(self, timeout=TIMEOUT_CMD):
        """Les svar fraa EP1 IN."""
        try:
            svar = self._dev.read(EP_CMD_IN, 512, timeout=timeout)
            data = bytes(svar)
            log.debug(f"EP1 IN  [{len(data)}B]: {data[:32].hex()}{'...' if len(data) > 32 else ''}")
            return data
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved lesing fra EP1 IN: {e}") from e

    # ---- Enkle kommandoer (send + les svar, ingen B1-poll) ----

    def send_raa_kommando(self, data, timeout=TIMEOUT_CMD):
        """Send vilkaarleg kommando og les svar (uten B1-poll)."""
        with self._cmd_lock:
            self._send_cmd(data)
            return self._les_svar(timeout=timeout)

    def les_fw_versjon(self):
        """Send 0x00, les firmware-versjon."""
        svar = self.send_raa_kommando(bytes([OPCODE_FW_VERSJON]))
        log.info(f"FW-versjon raa: {svar[:8].hex()}")
        return svar

    def sett_aktiv_modus(self):
        """Send A0 01, aktiver enheten."""
        svar = self.send_raa_kommando(bytes([OPCODE_SETMODE, 0x01]))
        log.info("Aktiv modus satt")
        return svar

    def hent_slot_tilstedevaerelse(self):
        """Send A1, hent slot-tilstedevaerelses-kart."""
        svar = self.send_raa_kommando(bytes([OPCODE_GETCONFIG]))
        log.info(f"Slot-tilstedevaerelse: {svar[:16].hex()}")
        return svar

    def hent_slot_typer(self):
        """Send AC, hent slot-type-kart.

        Returns:
            bytes: Type per slot (0x04=analog, 0x06=digital, 0x00=tom)
        """
        svar = self.send_raa_kommando(bytes([OPCODE_GETSLOTTYPES]))
        log.info(f"Slot-typer: {svar[:16].hex()}")
        return svar

    def les_eeprom(self, addr_hi=0x00, addr_lo=0x00):
        """Les EEPROM-data (enhetsinfo, serienummer, kalibrering)."""
        return self.send_raa_kommando(bytes([OPCODE_EEPROM, 0x1E, addr_hi, addr_lo]))

    def les_lisens_eeprom(self):
        """Les lisensblokka frå EEPROM (side 0x21, offset 0x40-0xBF).

        Returnerer rå binærdata (130 bytes) som kan skrivast til system_ds.lic.
        Strukturen er:
          0x40: 8 bytes header (checksum + datalen)
          0x48: 16 bytes hash
          0x58: 16 bytes lisensnøkkel (t.d. DWLQ8JP9YCQD311 + checksum)
          0x68-0xBF: lisensoppføringar (serienummer, hardware-ID-ar)
        """
        # Same EEPROM-adresser som DewesoftX les i pcapng
        reads = [
            (0x21, 0x40, 0x08),  # Header
            (0x21, 0x48, 0x10),  # Hash
            (0x21, 0x58, 0x10),  # Lisensnøkkel
            (0x21, 0x68, 0x10),  # Lisensoppføringar
            (0x21, 0x78, 0x10),
            (0x21, 0x88, 0x10),
            (0x21, 0x98, 0x10),
            (0x21, 0xA8, 0x10),
            (0x21, 0xB8, 0x10),
        ]
        lisens_data = bytearray()
        for page, offset, length in reads:
            svar = self.send_raa_kommando(
                bytes([OPCODE_EEPROM, page, offset, length])
            )
            # Svar er 64 bytes; berre dei fyrste `length` bytes er nyttedata
            lisens_data.extend(svar[:length])
        log.info(f"Lisens-EEPROM lese: {len(lisens_data)} bytes")
        return bytes(lisens_data)

    def init_kommando(self):
        """Send B0 3F 0C, initialiser/reset enheten."""
        svar = self.send_raa_kommando(bytes([OPCODE_INIT, 0x3F, 0x0C]))
        log.info("Init-kommando sendt")
        return svar

    def send_telemetri(self):
        """Send AE telemetri-kommando (heartbeat) og les svar.

        MERK: DewesoftX bruker IKKJE 0xAE for heartbeat under streaming.
        DewesoftX sender 0xB1 (poll) kontinuerleg (~120-200/sek).
        Denne metoden brukast berre under init/diagnostikk.
        """
        return self.send_raa_kommando(bytes([OPCODE_TELEMETRI, 0x1F, 0x0C]))

    def poll_b1(self, timeout=10):
        """Send B1 poll-kommando (heartbeat) med kort timeout.

        DewesoftX sender dette ~120-200 gonger/sek under streaming.
        Returnerer svar-bytes eller None viss timeout/ingen data.
        """
        with self._cmd_lock:
            try:
                self._dev.write(EP_CMD_OUT, bytes([OPCODE_POLL]), timeout=100)
            except Exception as e:
                raise SiriusUSBFeil(f"B1 poll write feilet: {e}") from e
            try:
                svar = self._dev.read(EP_CMD_IN, 64, timeout=timeout)
                return bytes(svar)
            except Exception as e:
                feil_str = str(e).lower()
                if "timeout" in feil_str or "timed out" in feil_str:
                    return None  # Ingen data — normalt
                raise SiriusUSBFeil(f"B1 poll read feilet: {e}") from e

    def send_prestart(self):
        """Send A4 00 pre-start kommando (foer start-sekvens)."""
        svar = self.send_raa_kommando(bytes([OPCODE_PRESTART, 0x00]))
        log.info("Pre-start (A4 00) sendt")
        return svar

    # ---- Eldre FX2-kommandoer (beholdt for kompatibilitet) ----

    def les_enhetsid(self):
        """Send 0xE3 og les enhetsidentifikasjon (FX2-lag)."""
        svar = self.send_raa_kommando(bytes([OPCODE_IDENT]))
        resultat = {'raa': svar}
        try:
            tekst = svar.rstrip(b'\x00').decode('ascii', errors='replace')
            resultat['enhetsstreng'] = tekst[:8]
            resultat['serienummer'] = tekst[8:].strip('\x00').strip()
        except Exception:
            resultat['enhetsstreng'] = ""
            resultat['serienummer'] = ""
        return resultat

    def les_enhetstype(self):
        """Send 0xE4 og les enhetstype (FX2-lag)."""
        return self.send_raa_kommando(bytes([OPCODE_DEVTYPE]))

    # ---- AD-kommandoer med B1-poll ----

    @staticmethod
    def _bygg_ad_kommando(op_type, slot, register, data=None):
        """
        Bygg ein 15-byte 0xAD kommando.

        Format:
        AD 3F 0C 00 00 00 [op] [p0 p1 p2] [slot] [reg] [d0] [d1] [d2]

        For skriving (op=0x13): padding=000000, reg og data er eksplisitte
        For lesing (op=0x14): padding=000000, reg=0xFF, data=0xFFFFFF
        For query/batch (op=0x08/0x1C): padding=FFFFFF, slot=0xFF, reg=0xFF, data=0xFFFFFF
        For enum (op=0x0C): padding=000000, reg=0xFF, data=0xFFFFFF
        """
        if data is None:
            if op_type in (OP_LES, OP_SLOT_QUERY, OP_BATCH, OP_SLOT_ENUM):
                data = bytes([0xFF, 0xFF, 0xFF])
            else:
                data = bytes(3)
        elif isinstance(data, int):
            data = struct.pack('<I', data)[:3]
        elif len(data) < 3:
            data = data + bytes(3 - len(data))

        # Padding bytes 7-9: 0xFF for global ops, 0x00 ellers
        padding = bytes([0xFF, 0xFF, 0xFF]) if op_type in (OP_SLOT_QUERY, OP_BATCH) else bytes(3)

        cmd = bytes([
            OPCODE_AD,   # 0
            0x3F,        # 1
            0x0C,        # 2
            0x00,        # 3
            0x00,        # 4
            0x00,        # 5
            op_type,     # 6
            padding[0],  # 7
            padding[1],  # 8
            padding[2],  # 9
            slot,        # 10
            register,    # 11
            data[0],     # 12
            data[1],     # 13
            data[2],     # 14
        ])
        return cmd

    @staticmethod
    def _bygg_b1_kommando():
        """Bygg ein 1-byte 0xB1 poll-kommando."""
        return bytes([OPCODE_POLL])

    @staticmethod
    def _bygg_ae_kommando():
        """Bygg ein 3-byte 0xAE telemetri-kommando."""
        return bytes([OPCODE_TELEMETRI, 0x1F, 0x0C])

    def send_ad_og_poll(self, op_type, slot, register, data=None, maks_forsok=MAKS_POLL_FORSOK):
        """
        Kjerneoperasjonen: Send AD-kommando og poll for svar.

        Sekvens:
          1. Send 0xAD paa EP1 OUT
          2. Les EP1 IN -> forvent all-0xFF (ACK)
          3. Send 0xB1 paa EP1 OUT
          4. Les EP1 IN -> for skriving: aksepter med ein gong;
             for lesing: sjekk byte[0]=1 (klar), 0=proev igjen
          5. Returner heile svar-bufferet

        Returns:
            bytes: Heile poll-svaret (inkludert status-byte)
        """
        with self._cmd_lock:
            # 1. Send AD-kommando
            ad_cmd = self._bygg_ad_kommando(op_type, slot, register, data)
            self._send_cmd(ad_cmd)

            # 2. Les ACK (forvent all-0xFF)
            ack = self._les_svar(timeout=TIMEOUT_CMD)
            if not all(b == ACK_BYTE for b in ack[:4]):
                log.debug(f"ACK: {ack[:8].hex()}")

            # 3-4. Poll
            er_skriving = (op_type == OP_SKRIV)

            for forsok in range(maks_forsok):
                self._send_cmd(self._bygg_b1_kommando())
                svar = self._les_svar(timeout=TIMEOUT_POLL)

                if len(svar) < 2:
                    log.debug(f"Poll #{forsok + 1}: kort svar ({len(svar)}B)")
                    continue

                status_byte = svar[0]

                if status_byte == POLL_KLAR:
                    # Leseresultat klar
                    return bytes(svar)
                elif er_skriving:
                    # Skriving: byte[0]=0 er normal bekreftelse
                    return bytes(svar)
                elif status_byte == POLL_VENT:
                    log.debug(f"Poll #{forsok + 1}: venter...")
                    continue
                else:
                    # Ukjent status - aksepter som svar
                    log.debug(f"Poll #{forsok + 1}: status=0x{status_byte:02X}")
                    return bytes(svar)

            raise SiriusPollTimeout(
                f"Poll timeout etter {maks_forsok} forsok "
                f"(op=0x{op_type:02X}, slot=0x{slot:02X}, reg=0x{register:02X})"
            )

    def send_ad_raa_og_poll(self, ad_cmd, maks_forsok=MAKS_POLL_FORSOK,
                            er_skriving=True):
        """
        Send ferdigbygd AD-kommando og poll med B1 til ferdig.

        Brukt for globale register-kommandoar i start-sekvensen.
        Desse har formatet: AD 3F 0C 00 00 00 [reg] [8 bytes data]
        og fylgjer same ACK + B1-poll syklus som vanlege AD-kommandoar.

        For skrivingar: aksepterer B1-svar med status=0x00 (skrivebekreftelse)
        med ein gong, same som send_ad_og_poll gjer for op=0x13.
        For lesingar (er_skriving=False): ventar paa status=0x01 (data klar).
        Trigger-register (0x02) treng mange forsok (~1000).

        Args:
            ad_cmd: Ferdigbygde 15 bytes AD-kommando
            maks_forsok: Maks antal B1-poll-forsok
            er_skriving: True for register-skriving (aksepter 0x00),
                         False for lesing (vent paa 0x01)

        Returns:
            bytes: Poll-svaret

        Raises:
            SiriusPollTimeout: Viss maks_forsok er naadd
        """
        with self._cmd_lock:
            self._send_cmd(ad_cmd)

            ack = self._les_svar(timeout=TIMEOUT_CMD)
            if not all(b == ACK_BYTE for b in ack[:4]):
                log.debug(f"ACK: {ack[:8].hex()}")

            for forsok in range(maks_forsok):
                self._send_cmd(self._bygg_b1_kommando())
                svar = self._les_svar(timeout=TIMEOUT_POLL)

                if len(svar) < 2:
                    continue

                if svar[0] == POLL_KLAR:
                    return bytes(svar)
                elif svar[0] == POLL_VENT:
                    if er_skriving:
                        # Skriving: 0x00 er normal bekreftelse (same som send_ad_og_poll)
                        return bytes(svar)
                    continue
                else:
                    return bytes(svar)

            raise SiriusPollTimeout(
                f"Poll timeout etter {maks_forsok} forsok "
                f"(raa cmd: {ad_cmd[:8].hex()}...)"
            )

    # ---- AD hoeynivaa-wrappers ----

    def skriv_register(self, slot, reg, verdi):
        """Skriv ein verdi til eit register via AD op=0x13."""
        return self.send_ad_og_poll(OP_SKRIV, slot, reg, verdi)

    def les_register(self, slot, reg=0xFF):
        """Les fraa eit register via AD op=0x14.

        Registeret er som regel 0xFF (les resultat av siste operasjon).
        """
        return self.send_ad_og_poll(OP_LES, slot, reg)

    def slot_query(self):
        """AD op=0x08 slot=0xFF: Global slot-query."""
        return self.send_ad_og_poll(OP_SLOT_QUERY, 0xFF, 0xFF)

    def slot_enum(self, slot):
        """AD op=0x0C: Enumerer ein spesifikk slot."""
        return self.send_ad_og_poll(OP_SLOT_ENUM, slot, 0xFF)

    def batch_op(self):
        """AD op=0x1C slot=0xFF: Batch/global operasjon."""
        return self.send_ad_og_poll(OP_BATCH, 0xFF, 0xFF)

    # ---- A5 kommando-dispatch hjelparar ----

    def a5_set_param(self, slot, param, enable=True):
        """Skriv A5 [01 param 5A] - sett parameter med skrivenoekkelen."""
        data = bytes([A5_SET_PARAM, param, WRITE_KEY if enable else 0x00])
        return self.skriv_register(slot, REG_CMD, data)

    def a5_set_mode(self, slot, mode_reg, value):
        """Skriv A5 [02 mode_reg value] - sett modus."""
        data = bytes([A5_SET_MODE, mode_reg, value])
        return self.skriv_register(slot, REG_CMD, data)

    def a5_read_string(self, slot, string_reg, offset):
        """Skriv A5 [03 string_reg offset] - forbered strenglesing."""
        data = bytes([A5_READ_STRING, string_reg, offset])
        return self.skriv_register(slot, REG_CMD, data)

    def a5_set_config(self, slot, config_reg, value):
        """Skriv A5 [06 config_reg value] - utvidet konfigurasjon."""
        data = bytes([A5_SET_CONFIG, config_reg, value])
        return self.skriv_register(slot, REG_CMD, data)

    def commit(self, slot):
        """Skriv 5A [00 00 00] - commit/trigger."""
        return self.skriv_register(slot, REG_COMMIT, COMMIT_DATA)

    def trigger_read(self, slot, trigger_reg):
        """Skriv [trigger_reg] [5A 00 00] - trigger ei lesing."""
        return self.skriv_register(slot, trigger_reg, TRIGGER_DATA)

    # ---- Dataendepunkt ----

    def les_adc_data(self, storrelse=16384, timeout=TIMEOUT_DATA):
        """Les raa ADC-data fraa EP2."""
        try:
            data = self._dev.read(EP_ADC_IN, storrelse, timeout=timeout)
            return bytes(data)
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved lesing av ADC-data (EP2): {e}") from e

    def les_sync_data(self, storrelse=512, timeout=TIMEOUT_DATA):
        """Les sync/tidsstempel-data fraa EP6."""
        try:
            data = self._dev.read(EP_SYNC_IN, storrelse, timeout=timeout)
            return bytes(data)
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved lesing av sync-data (EP6): {e}") from e

    def les_ctrl_data(self, storrelse=512, timeout=TIMEOUT_CMD):
        """Les kontrolldata fraa EP4."""
        try:
            data = self._dev.read(EP_CTRL_IN, storrelse, timeout=timeout)
            return bytes(data)
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved lesing av ctrl-data (EP4): {e}") from e

    def flush_endepunkt(self, ep, forsok=5):
        """Toem eit endepunkt ved aa lese til timeout."""
        for _ in range(forsok):
            try:
                self._dev.read(ep, 512, timeout=100)
            except Exception:
                break
