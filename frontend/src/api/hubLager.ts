import { apiGet, apiPut } from './client'

export interface HubLagerNode {
  node_id: string
  node_namn: string
  rader: number
  siste_ts: number | null
}

export interface HubLagerKonfig {
  aktivert: boolean
  db_sti: string
  retensjon_dagar: number
  min_intervall_s: number
  maks_mb: number
  // Køyrestatus (frå konfig_offentleg / status)
  rader?: number
  storleik_mb?: number
  kø_lengd?: number
  lagra_totalt?: number
  droppa_full?: number
  droppa_throttle?: number
  siste_feil?: string
  eldste_ts?: number | null
  nyaste_ts?: number | null
  nodar?: HubLagerNode[]
}

export const fetchHubLagerKonfig = () =>
  apiGet<HubLagerKonfig>('/api/hub-lager/konfig')

export const lagreHubLagerKonfig = (k: Partial<HubLagerKonfig>) =>
  apiPut<{ suksess: boolean } & HubLagerKonfig>('/api/hub-lager/konfig', k)

export const fetchHubLagerStatus = () =>
  apiGet<HubLagerKonfig>('/api/hub-lager/status')

export const hubLagerCsvUrl = (node_id = '') =>
  `/api/hub-lager/eksport.csv${node_id ? `?node_id=${encodeURIComponent(node_id)}` : ''}`
