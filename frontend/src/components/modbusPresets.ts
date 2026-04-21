import type { ModbusRegister } from '../api/types'

export interface ModbusPreset {
  id: string
  namn: string
  beskriving: string
  port: number
  unit_id: number
  poll_hz: number
  timeout_ms: number
  base_adresse: number
  registers: ModbusRegister[]
  /** Synleg hjelpe-melding om korleis brukar skal fylle inn adresser. */
  hjelp: string
}

function reg(
  namn: string,
  offset: number,
  eining: string,
  range_low: number,
  range_high: number,
): ModbusRegister {
  return {
    namn,
    adresse: offset,
    funksjon: 'holding',
    datatype: 'float32',
    byte_order: 'AB_CD',   // BIG_ENDIAN — PQube 3 standard
    skalering: 1.0,         // PQube 3 leverer ferdig skalerte verdiar
    offset: 0.0,
    eining,
    range_low,
    range_high,
  }
}

/**
 * PQube 3 preset — vanlege målingar frå Classic Register Bank (offset 0x0).
 *
 * Offsets er frå "PQube 3 Modbus Reference Manual V1.11", kapittel 3.1.
 * Base-adresse = 7000. Absolutt adresse = 7000 + offset.
 */
const PQUBE_3_PRESET: ModbusPreset = {
  id: 'pqube3',
  namn: 'Powerside PQube 3',
  beskriving: 'Power-kvalitet-analysator (Classic Register Bank). Offsets frå Modbus Reference Manual V1.11.',
  port: 502,
  unit_id: 1,
  poll_hz: 1.0,
  timeout_ms: 2000,
  base_adresse: 7000,
  hjelp: 'Offsets er ferdig utfylt frå PQube 3 Classic Register Bank. Slett rader du ikkje treng. Verdiar er ferdig-skalerte (V, A, Hz, W, VA).',
  registers: [
    // Spenningar L-N (V, RMS)
    reg('V_L1_N',   8,  'V',   0,  500),
    reg('V_L2_N',   10, 'V',   0,  500),
    reg('V_L3_N',   12, 'V',   0,  500),
    // Spenningar L-L (V, RMS)
    reg('V_L1_L2',  14, 'V',   0,  900),
    reg('V_L2_L3',  16, 'V',   0,  900),
    reg('V_L3_L1',  18, 'V',   0,  900),
    // Frekvens (Hz)
    reg('Frekvens', 26, 'Hz',  45, 65),
    // Fasestrømmar (A, RMS)
    reg('I_L1',     28, 'A',   0,  1000),
    reg('I_L2',     30, 'A',   0,  1000),
    reg('I_L3',     32, 'A',   0,  1000),
    reg('I_N',      34, 'A',   0,  1000),
    // Effekt totalt (W) og tilsynelatande effekt (VA)
    reg('P_total',  36, 'W',   -1000000, 1000000),
    reg('S_total',  38, 'VA',  0,  1000000),
  ],
}

export const MODBUS_PRESETS: ModbusPreset[] = [
  PQUBE_3_PRESET,
]

export function hentPreset(id: string): ModbusPreset | undefined {
  return MODBUS_PRESETS.find(p => p.id === id)
}

/** Returnerer true viss register-lista har minst ein register med adresse=0 (ikkje utfylt). */
export function harUtfyltAdresser(registers: ModbusRegister[]): boolean {
  return registers.every(r => r.adresse > 0)
}
