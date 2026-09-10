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
