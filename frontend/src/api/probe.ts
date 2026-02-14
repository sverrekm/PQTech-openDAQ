import { apiGet, apiPost } from './client'
import type { ProbeResult, ActionResult, Rapport } from './types'

export const fetchProbeStatus = () => apiGet<ProbeResult>('/api/probe/status')

export const kjorProbe = () => apiPost<ActionResult>('/api/probe/kjor')

export const kjorProtokoll = (modus: string) =>
  apiPost<ActionResult>('/api/probe/protokoll', { modus })

export const kjorDekoder = (modus: string) =>
  apiPost<ActionResult>('/api/probe/dekoder', { modus })

export const kjorAdc = (varighet: number, lagre = false) =>
  apiPost<ActionResult>('/api/probe/adc', { varighet, lagre })

export const kjorSniffer = (varighet: number) =>
  apiPost<ActionResult>('/api/probe/sniffer', { varighet })

export const fetchRapporter = () =>
  apiGet<{ rapporter: Rapport[] }>('/api/probe/rapporter')
