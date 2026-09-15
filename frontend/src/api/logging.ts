import { apiGet, apiPut } from './client'

/**
 * Per-node logge-styring på hubben: om måledata frå ein node vert LAGRA
 * (hub_lager + NAS). Live-visninga er ikkje råka.
 */
export type LoggModus = 'kontinuerleg' | 'av' | 'planlagt'

export interface NodeLoggInnstilling {
  modus: LoggModus
  start_ms: number   // epoch ms; 0 = ope
  slutt_ms: number   // epoch ms; 0 = ope
}

export interface LoggingNode {
  id: string
  namn: string
  loggar_no: boolean
}

export interface HubLogging {
  innstillingar: Record<string, NodeLoggInnstilling>  // key = node-namn
  nodar: LoggingNode[]
  server_ms: number
}

export const fetchHubLogging = () => apiGet<HubLogging>('/api/hub/logging')

export const setNodeLogging = (
  node_namn: string, modus: LoggModus, start_ms = 0, slutt_ms = 0,
) => apiPut<{ suksess: boolean; melding: string }>('/api/hub/logging', {
  node_namn, modus, start_ms, slutt_ms,
})
