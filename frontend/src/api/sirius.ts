import { apiGet, apiPost } from './client'
import type { SiriusStatus, SiriusData, ActionResult } from './types'

export const fetchSiriusStatus = () => apiGet<SiriusStatus>('/api/sirius/status')

export const fetchSiriusData = () => apiGet<SiriusData>('/api/sirius/data')

export const siriusStart = (sampleRate?: number, kanaler?: number[]) =>
  apiPost<ActionResult>('/api/sirius/start', { sample_rate: sampleRate, kanaler })

export const siriusStopp = () => apiPost<ActionResult>('/api/sirius/stopp')

export const siriusRekoble = () => apiPost<ActionResult>('/api/sirius/rekoble')

export const siriusGjenopplivEp2 = () => apiPost<ActionResult>('/api/sirius/gjenoppliv-ep2')
