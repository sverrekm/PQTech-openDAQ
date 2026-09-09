import { apiPost } from './client'

/**
 * SunSpec — den opne standarden dei fleste invertarar snakkar over Modbus.
 *
 * Poenget: ein open port 502 fortel berre at det er Modbus. SunSpec fortel
 * kva registera BETYR, so vi kan lage ferdige kanalar i staden for at
 * brukaren må slå opp adresser i eit datablad.
 */
export interface SunSpecModell {
  id: number
  namn: string
  adresse: number
  lengd: number
  /** Vi har punktdefinisjonar for denne modellen */
  kan_lese?: boolean
}

export interface SunSpecInfo {
  /** Basisadressa signaturen vart funnen på (40000 / 50000 / 0) */
  base?: number
  produsent?: string
  modell?: string
  serienr?: string
  firmware?: string
  modellar?: SunSpecModell[]
  /** Minst éin modell vi kan byggje kanalar av */
  kan_lese?: boolean
}

export interface LeggTilSvar {
  suksess: boolean
  melding: string
  namn?: string
  tal?: number
  /** Punkt eininga melder som «ikkje implementert» */
  hoppa?: string[]
}

/** Bygg kanalar av eininga og legg dei inn som ein Modbus-node. */
export const leggTilSunSpec = (host: string, base?: number, port = 502) =>
  apiPost<LeggTilSvar>('/api/sunspec/legg-til', { host, port, base })
