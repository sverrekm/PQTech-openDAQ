import { apiGet, apiPut } from './client'

export interface RaaFilKonfig {
  aktivert: boolean
  katalog: string
  // status
  skrive_totalt?: number
  droppa?: number
  kø_lengd?: number
  siste_skriv_ts?: number
  siste_feil?: string
  katalog_finst?: boolean
  skrivbar?: boolean
}

export const fetchRaaFilKonfig = () =>
  apiGet<RaaFilKonfig>('/api/raa-fil/konfig')

export const lagreRaaFilKonfig = (k: Partial<RaaFilKonfig>) =>
  apiPut<{ suksess: boolean } & RaaFilKonfig>('/api/raa-fil/konfig', k)
