import { apiGet, apiPost, apiPut } from './client'
import type { VersjonInfo, OppdateringSjekk, OppdateringsResultat } from './types'

export const fetchVersjon = () => apiGet<VersjonInfo>('/api/system/versjon')
export const sjekkOppdatering = () => apiGet<OppdateringSjekk>('/api/system/sjekk-oppdatering')
export const utfoerOppdatering = () => apiPost<OppdateringsResultat>('/api/system/oppdater')

export interface FloateResultat {
  nodar: { id: string; namn: string; suksess: boolean; melding?: string; feil?: string }[]
  hub: OppdateringsResultat | null
  feil?: string
}
export const utfoerOppdateringFloate = () => apiPost<FloateResultat>('/api/system/oppdater-floate')
export const restartSystem = () => apiPost<{ suksess: boolean; melding: string }>('/api/system/restart')

export interface OppdaterKonfig {
  repo_url: string
  branch: string
  token_satt: boolean
}

export interface LagreKonfigResultat {
  suksess: boolean
  melding?: string
  feil?: string
  repo_url?: string
  branch?: string
  token_satt?: boolean
}

export const fetchOppdaterKonfig = () => apiGet<OppdaterKonfig>('/api/system/oppdater-konfig')

// token utelatt => behald eksisterande; tom streng => fjern
export const lagreOppdaterKonfig = (repo_url: string, branch: string, token?: string) =>
  apiPut<LagreKonfigResultat>(
    '/api/system/oppdater-konfig',
    token === undefined ? { repo_url, branch } : { repo_url, branch, token },
  )
