import type { ModbusRegister } from '../api/types'

export interface ModbusPreset {
  id: string
  namn: string
  beskriving: string
  port: number
  unit_id: number
  poll_hz: number
  timeout_ms: number
  registers: ModbusRegister[]
  /** Synleg hjelpe-melding om korleis brukar skal fylle inn adresser. */
  hjelp: string
}

// Felles skeleton-adresse — 0 betyr "fyll inn frå manual". UI skal advare mot å teste/lagre
// register med adresse 0 før brukar har oppdatert den.
const TODO_ADRESSE = 0

function reg(namn: string, eining: string, range_low: number, range_high: number): ModbusRegister {
  return {
    namn,
    adresse: TODO_ADRESSE,
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
 * PQube 3 preset — skeleton med vanlege målingar.
 *
 * Adresser: 0 (må fyllast inn frå PQube 3 Modbus Reference Manual).
 * Base-adresse på PQube 3 er 0x7000 (28672) — legg dette til offset frå tabellen.
 * Byte-order: AB_CD (BIG_ENDIAN) — PQube 3 standard.
 */
const PQUBE_3_PRESET: ModbusPreset = {
  id: 'pqube3',
  namn: 'Powerside PQube 3',
  beskriving: 'Power-kvalitet-analysator. Fyll inn register-adresser frå Modbus Reference Manual.',
  port: 502,
  unit_id: 1,
  poll_hz: 1.0,
  timeout_ms: 2000,
  hjelp: 'Base-adresse er 0x7000 (28672). Offset frå Modbus Reference Manual-tabellen vert lagt til 28672. Døme: offset 8-9 (L1-N) vert adresse 28680.',
  registers: [
    // Spenningar L-N
    reg('V_L1_N', 'V', 0, 500),
    reg('V_L2_N', 'V', 0, 500),
    reg('V_L3_N', 'V', 0, 500),
    // Spenningar L-L
    reg('V_L1_L2', 'V', 0, 900),
    reg('V_L2_L3', 'V', 0, 900),
    reg('V_L3_L1', 'V', 0, 900),
    // Strømmar
    reg('I_L1', 'A', 0, 1000),
    reg('I_L2', 'A', 0, 1000),
    reg('I_L3', 'A', 0, 1000),
    // Frekvens
    reg('Frekvens', 'Hz', 45, 65),
    // Effekt totalt
    reg('P_total', 'W', -1000000, 1000000),
    reg('Q_total', 'var', -1000000, 1000000),
    reg('S_total', 'VA', 0, 1000000),
    reg('PF_total', '', -1, 1),
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
