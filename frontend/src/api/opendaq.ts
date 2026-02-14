import { apiGet, apiPost } from './client'
import type { OpenDaqStatus, OpenDaqVerdiar, ActionResult } from './types'

export const fetchOpenDaqStatus = () => apiGet<OpenDaqStatus>('/api/opendaq/status')

export const fetchOpenDaqVerdiar = () => apiGet<OpenDaqVerdiar>('/api/opendaq/verdiar')

export const restartOpenDaq = () => apiPost<ActionResult>('/api/opendaq/restart')
