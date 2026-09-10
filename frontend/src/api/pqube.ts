import { apiPost } from './client'

/** PQube 3 — power-kvalitet-analysator på Modbus TCP (port 502). */
export interface PqubeInfo {
  pqube: boolean
  host?: string
  verdiar?: Record<string, number>
  melding?: string
}

export const oppdagPqube = (host: string, port = 502) =>
  apiPost<PqubeInfo>('/api/pqube/oppdag', { host, port })

/** Legg PQube-en inn som ein modbus_tcp-node med ferdig register-kart. */
export const leggTilPqube = (host: string, namn = '', port = 502) =>
  apiPost<{ suksess: boolean; melding: string; namn?: string; tal?: number }>(
    '/api/pqube/legg-til', { host, namn, port })

// --- Elspec G4500 (via RS-485→Modbus-TCP-gateway, unit 159) ---
export interface G4500Info {
  g4500: boolean
  host?: string
  funksjon?: string
  verdiar?: Record<string, number>
  melding?: string
}
export const oppdagG4500 = (host: string, unit_id = 159, port = 502) =>
  apiPost<G4500Info>('/api/g4500/oppdag', { host, unit_id, port })
export const leggTilG4500 = (host: string, unit_id = 159, namn = '', port = 502) =>
  apiPost<{ suksess: boolean; melding: string; namn?: string; tal?: number }>(
    '/api/g4500/legg-til', { host, unit_id, namn, port })
