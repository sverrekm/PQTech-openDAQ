import { apiGet, apiPut } from './client'

/** Push-konfig (kva parent denne containeren pushar til + sjølv-identitet).
 *  node_namn er namnet som taggar målingane (Grafana/hub/rapportar). */
export interface PushKonfig {
  parent_url: string
  parent_token: string
  node_id: string
  node_namn: string
  push_hz: number
  accept_ingest: boolean
  ingest_token: string
  verdi_type?: string
  samples_per_pakke?: number
}

export const fetchPushKonfig = () => apiGet<PushKonfig>('/api/push/konfig')

export const oppdaterPushKonfig = (k: PushKonfig) =>
  apiPut<{ suksess: boolean; melding: string }>('/api/push/konfig', k)

/** Live-status for utgåande push (node → hub). */
export interface PushStatus {
  konfigurert: boolean
  kjorer: boolean
  parent_url?: string
  node_namn?: string
  push_hz?: number
  sendt_ok?: number
  sendt_feil?: number
  siste_status_kode?: number | null
  siste_feilmelding?: string
  siste_send_ts?: number
  siste_latens_ms?: number
  feil?: string
}

export const fetchPushStatus = () => apiGet<PushStatus>('/api/push/status')
