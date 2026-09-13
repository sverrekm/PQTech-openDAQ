import { apiGet, apiPut, apiPost } from './client'

export interface PqzipStatus {
  tilstand: string
  melding: string
  totalt_henta: number
  lokalt_tal: number
  lokalt_mb: number
  nye_sist: number
  neste_om_s: number | null
}
export interface PqzipKonfig {
  aktivert: boolean
  rot: string
  monster: string
  maalkatalog: string
  intervall_min: number
  retensjon_dagar: number
  maks_mb: number
  status?: PqzipStatus
}

export const fetchPqzip = () => apiGet<PqzipKonfig>('/api/pqzip')
export const lagrePqzip = (k: Partial<PqzipKonfig>) =>
  apiPut<{ suksess: boolean; melding: string } & PqzipKonfig>('/api/pqzip', k)
export const synkPqzip = () =>
  apiPost<{ suksess: boolean; melding: string }>('/api/pqzip/synk')
