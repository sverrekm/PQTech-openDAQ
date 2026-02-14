import { apiGet, apiPut, apiPost } from './client'
import type { KanalKonfig, KanalLive, ActionResult } from './types'

export const fetchKanalar = () => apiGet<KanalKonfig[]>('/api/kanalar')

export const oppdaterKanalar = (konfig: KanalKonfig[]) =>
  apiPut<ActionResult>('/api/kanalar', konfig)

export const resetKanalar = () => apiPost<ActionResult>('/api/kanalar/reset')

export const fetchKanalLive = () => apiGet<KanalLive>('/api/kanalar/live')
