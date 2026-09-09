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

export interface HubLagerPunkt {
  node_id: string
  node_namn: string
  kanal: string
  verdi: number
  ts_ms: number
}

/** Tidsserie for éin kanal (node_id + kanalnamn) i eit tidsvindauge. */
export const fetchHubLagerData = (
  node_id: string, kanal: string, fra_ms: number, til_ms = 0, limit = 2000,
) => {
  const p = new URLSearchParams({ node_id, kanal, fra_ms: String(fra_ms), limit: String(limit) })
  if (til_ms) p.set('til_ms', String(til_ms))
  return apiGet<{ rader: HubLagerPunkt[] }>(`/api/hub-lager/data?${p.toString()}`)
}
