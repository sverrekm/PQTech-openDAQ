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
