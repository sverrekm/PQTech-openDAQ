import { apiGet, apiPost } from './client'
import type { VersjonInfo, OppdateringSjekk, OppdateringsResultat } from './types'

export const fetchVersjon = () => apiGet<VersjonInfo>('/api/system/versjon')
export const sjekkOppdatering = () => apiGet<OppdateringSjekk>('/api/system/sjekk-oppdatering')
export const utfoerOppdatering = () => apiPost<OppdateringsResultat>('/api/system/oppdater')
