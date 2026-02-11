#!/usr/bin/env python3
"""
SIRIUS USB-protokoll implementasjon (Lag 1)
=============================================
Lavnivaa protokoll-lag som bygger kommandoer, sender via pyusb, og parser svar.

Basert paa reverse-engineering av DewesoftX USB-trafikk via usbmon.

Protokollen bruker 3 kommandotyper:
  0xAD: Register les/skriv (15 bytes)
  0xB1: Poll (1 byte)
  0xAE: Telemetri/heartbeat (3 bytes)

Kjerneoperasjonen (AD + poll):
  1. Send 0xAD paa EP1 OUT
  2. Les EP1 IN -> forvent all-0xFF (ACK)
  3. Send 0xB1 paa EP1 OUT
  4. Les EP1 IN -> sjekk word[0]: 1=klar, 0=proev igjen
  5. Returner data fra word[1..] naar klar
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

# --- Opkoder ---
OPCODE_AD = 0xAD       # Register les/skriv
OPCODE_POLL = 0xB1     # Poll for svar
OPCODE_TELEMETRI = 0xAE  # Telemetri/heartbeat
OPCODE_IDENT = 0xE3    # Enhetsidentifikasjon
OPCODE_DEVTYPE = 0xE4  # Enhetstype

# --- Operasjonstyper for 0xAD ---
OP_SKRIV = 0x13   # Skriv til register
OP_LES = 0x14     # Les fra register

# --- Slotter (analoge inngangsmoduler) ---
SLOT_0 = 0x04
SLOT_1 = 0x05
SLOT_2 = 0x06
SLOT_3 = 0x07
ALLE_SLOTTER = [SLOT_0, SLOT_1, SLOT_2, SLOT_3]

# --- Kjente registre ---
REG_SLOT_INFO = 0xA5   # Slot-informasjon (sub-registre 0x01-0x06)
REG_ADC_KONFIG = 0x08  # ADC-konfigurasjon
REG_KALIBRERING = 0xCC  # Kalibreringsdata

# --- Timeouts (ms) ---
TIMEOUT_CMD = 1000    # Kommando send/motta
TIMEOUT_POLL = 2000   # Poll-venting
TIMEOUT_DATA = 5000   # Datastroemming
TIMEOUT_SHORT = 300   # Korte operasjoner

# --- Poll-konstanter ---
MAKS_POLL_FORSOK = 10
POLL_KLAR = 1
POLL_VENT = 0

# ACK-moenstre (alle 0xFF betyr "mottatt, venter")
ACK_BYTE = 0xFF


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
        """
        Initialiser protokollen med en pyusb-enhet.

        Args:
            dev: usb.core.Device - aapen pyusb-enhet
        """
        self._dev = dev
        self._cmd_lock = threading.Lock()
        self._lukket = False
        log.debug("SiriusProtokoll initialisert")

    @property
    def enhet(self):
        return self._dev

    # ---- Kommandobygging ----

    @staticmethod
    def _bygg_ad_kommando(op_type, slot, register, data=None):
        """
        Bygg en 15-byte 0xAD kommando.

        Format:
        AD 3F 0C 00 00 00 [op] 00 00 00 [slot] [reg] [d0] [d1] [d2]
        byte: 0  1  2  3  4  5   6   7  8  9   10    11   12   13  14

        Args:
            op_type: Operasjonstype (OP_SKRIV=0x13 eller OP_LES=0x14)
            slot: Slot-adresse (0x04-0x07)
            register: Registeradresse
            data: 3 bytes data (for skriv), eller None/bytes(3) for les
        """
        if data is None:
            data = bytes(3)
        elif isinstance(data, int):
            data = struct.pack('<I', data)[:3]
        elif len(data) < 3:
            data = data + bytes(3 - len(data))

        cmd = bytes([
            OPCODE_AD,   # 0: opkode
            0x3F,        # 1: fast
            0x0C,        # 2: fast
            0x00,        # 3: fast
            0x00,        # 4: fast
            0x00,        # 5: fast
            op_type,     # 6: operasjonstype
            0x00,        # 7: fast
            0x00,        # 8: fast
            0x00,        # 9: fast
            slot,        # 10: slot
            register,    # 11: register
            data[0],     # 12: data byte 0
            data[1],     # 13: data byte 1
            data[2],     # 14: data byte 2
        ])
        return cmd

    @staticmethod
    def _bygg_b1_kommando():
        """Bygg en 1-byte 0xB1 poll-kommando."""
        return bytes([OPCODE_POLL])

    @staticmethod
    def _bygg_ae_kommando():
        """Bygg en 3-byte 0xAE telemetri-kommando."""
        return bytes([OPCODE_TELEMETRI, 0x1F, 0x0C])

    # ---- Raa USB I/O ----

    def _send_cmd(self, data):
        """
        Send data paa EP1 OUT (kommandokanal).

        Args:
            data: bytes aa sende

        Raises:
            SiriusUSBFeil: Ved USB-feil
        """
        try:
            skrevet = self._dev.write(EP_CMD_OUT, data, timeout=TIMEOUT_CMD)
            log.debug(f"EP1 OUT [{len(data)}B]: {data.hex()}")
            return skrevet
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved sending paa EP1 OUT: {e}") from e

    def _les_svar(self, timeout=TIMEOUT_CMD):
        """
        Les svar fra EP1 IN (kommandokanal).

        Args:
            timeout: Timeout i millisekunder

        Returns:
            bytes: Mottatte data

        Raises:
            SiriusUSBFeil: Ved USB-feil
        """
        try:
            svar = self._dev.read(EP_CMD_IN, 512, timeout=timeout)
            data = bytes(svar)
            log.debug(f"EP1 IN  [{len(data)}B]: {data[:32].hex()}{'...' if len(data) > 32 else ''}")
            return data
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved lesing fra EP1 IN: {e}") from e

    # ---- Kjerneoperasjoner ----

    def send_ad_og_poll(self, op_type, slot, register, data=None, maks_forsok=MAKS_POLL_FORSOK):
        """
        Kjerneoperasjonen: Send AD-kommando og poll for svar.

        Sekvendiagram:
          1. Send 0xAD paa EP1 OUT
          2. Les EP1 IN -> forvent all-0xFF (ACK)
          3. Send 0xB1 paa EP1 OUT
          4. Les EP1 IN -> sjekk word[0]: 1=klar, 0=proev igjen
          5. Returner data fra word[1..] naar klar

        Args:
            op_type: OP_SKRIV eller OP_LES
            slot: Slot-adresse
            register: Registeradresse
            data: Data for skriv (3 bytes), None for les
            maks_forsok: Maks antall poll-runder

        Returns:
            bytes: Svardata (uten status-word)

        Raises:
            SiriusPollTimeout: Hvis enheten ikke svarer i tide
            SiriusUSBFeil: Ved USB-feil
        """
        with self._cmd_lock:
            # 1. Send AD-kommando
            ad_cmd = self._bygg_ad_kommando(op_type, slot, register, data)
            self._send_cmd(ad_cmd)

            # 2. Les ACK (forvent all-0xFF)
            ack = self._les_svar(timeout=TIMEOUT_CMD)
            if not all(b == ACK_BYTE for b in ack[:4]):
                log.warning(f"Uventet ACK: {ack[:8].hex()} (forventet FFFFFFFF)")

            # 3-4. Poll til klar
            for forsok in range(maks_forsok):
                poll_cmd = self._bygg_b1_kommando()
                self._send_cmd(poll_cmd)

                svar = self._les_svar(timeout=TIMEOUT_POLL)

                if len(svar) < 2:
                    log.debug(f"Poll #{forsok + 1}: kort svar ({len(svar)}B)")
                    continue

                # Sjekk status-word (foerste 2 bytes som little-endian uint16)
                status = struct.unpack_from('<H', svar, 0)[0]

                if status == POLL_KLAR:
                    # 5. Returner data etter status-word
                    return bytes(svar[2:])
                elif status == POLL_VENT:
                    log.debug(f"Poll #{forsok + 1}: venter...")
                    continue
                else:
                    log.debug(f"Poll #{forsok + 1}: ukjent status 0x{status:04X}")

            raise SiriusPollTimeout(
                f"Poll timeout etter {maks_forsok} forsok "
                f"(op=0x{op_type:02X}, slot=0x{slot:02X}, reg=0x{register:02X})"
            )

    # ---- Hoeynivaa wrappers ----

    def skriv_register(self, slot, reg, verdi):
        """
        Skriv en verdi til et register via AD-kommando.

        Args:
            slot: Slot-adresse (0x04-0x07)
            reg: Registeradresse
            verdi: int eller 3-byte verdi

        Returns:
            bytes: Svardata
        """
        return self.send_ad_og_poll(OP_SKRIV, slot, reg, verdi)

    def les_register(self, slot, reg):
        """
        Les en verdi fra et register via AD-kommando.

        Args:
            slot: Slot-adresse (0x04-0x07)
            reg: Registeradresse

        Returns:
            bytes: Registerdata
        """
        return self.send_ad_og_poll(OP_LES, slot, reg)

    def send_telemetri(self):
        """
        Send AE telemetri-kommando (heartbeat) og les svar.

        Returns:
            bytes: Telemetri-svar
        """
        with self._cmd_lock:
            ae_cmd = self._bygg_ae_kommando()
            self._send_cmd(ae_cmd)
            svar = self._les_svar(timeout=TIMEOUT_CMD)
            return svar

    def les_enhetsid(self):
        """
        Send 0xE3 og les enhetsidentifikasjon.

        Returns:
            dict: {'raa': bytes, 'enhetsstreng': str, 'serienummer': str}
        """
        with self._cmd_lock:
            self._send_cmd(bytes([OPCODE_IDENT]))
            svar = self._les_svar(timeout=TIMEOUT_CMD)

        resultat = {'raa': svar}

        # Parse "DEWEUSB7" + serienummer
        try:
            tekst = svar.rstrip(b'\x00').decode('ascii', errors='replace')
            resultat['enhetsstreng'] = tekst[:8]  # "DEWEUSB7"
            resultat['serienummer'] = tekst[8:].strip('\x00').strip()
        except Exception:
            resultat['enhetsstreng'] = ""
            resultat['serienummer'] = ""

        return resultat

    def les_enhetstype(self):
        """
        Send 0xE4 og les enhetstype.

        Returns:
            bytes: Enhetstype-data (4 bytes)
        """
        with self._cmd_lock:
            self._send_cmd(bytes([OPCODE_DEVTYPE]))
            svar = self._les_svar(timeout=TIMEOUT_CMD)
            return svar

    def les_adc_data(self, storrelse=512, timeout=TIMEOUT_DATA):
        """
        Les raa ADC-data fra EP2.

        Args:
            storrelse: Antall bytes aa lese (standard 512)
            timeout: Timeout i ms

        Returns:
            bytes: Raa ADC-data

        Raises:
            SiriusUSBFeil: Ved USB-feil eller timeout
        """
        try:
            data = self._dev.read(EP_ADC_IN, storrelse, timeout=timeout)
            return bytes(data)
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved lesing av ADC-data (EP2): {e}") from e

    def les_sync_data(self, storrelse=512, timeout=TIMEOUT_DATA):
        """
        Les sync/tidsstempel-data fra EP6.

        Args:
            storrelse: Antall bytes aa lese (standard 512)
            timeout: Timeout i ms

        Returns:
            bytes: Sync-data

        Raises:
            SiriusUSBFeil: Ved USB-feil eller timeout
        """
        try:
            data = self._dev.read(EP_SYNC_IN, storrelse, timeout=timeout)
            return bytes(data)
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved lesing av sync-data (EP6): {e}") from e

    def les_ctrl_data(self, storrelse=512, timeout=TIMEOUT_CMD):
        """
        Les kontrolldata fra EP4.

        Args:
            storrelse: Antall bytes aa lese
            timeout: Timeout i ms

        Returns:
            bytes: Kontrolldata
        """
        try:
            data = self._dev.read(EP_CTRL_IN, storrelse, timeout=timeout)
            return bytes(data)
        except Exception as e:
            raise SiriusUSBFeil(f"Feil ved lesing av ctrl-data (EP4): {e}") from e

    def flush_endepunkt(self, ep, forsok=5):
        """
        Toem et endepunkt ved aa lese til timeout.

        Args:
            ep: Endepunkt-adresse
            forsok: Maks antall leseforsoek
        """
        for _ in range(forsok):
            try:
                self._dev.read(ep, 512, timeout=100)
            except Exception:
                break
