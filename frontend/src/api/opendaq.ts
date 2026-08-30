import { apiGet, apiPost, apiPut } from './client'
import type { OpenDaqStatus, OpenDaqVerdiar, ActionResult } from './types'

export const fetchOpenDaqStatus = () => apiGet<OpenDaqStatus>('/api/opendaq/status')

export const fetchOpenDaqVerdiar = () => apiGet<OpenDaqVerdiar>('/api/opendaq/verdiar')

export const restartOpenDaq = () => apiPost<ActionResult>('/api/opendaq/restart')

export interface DeviceIdx {
  device_idx: number
  /** true = sett i konfig på noden, false = arva frå env OPENDAQ_DEVICE_IDX */
  fra_konfig?: boolean
  env_verdi?: number
}

export const fetchDeviceIdx = () => apiGet<DeviceIdx>('/api/opendaq/device-idx')

export const settDeviceIdx = (device_idx: number, restart = true) =>
  apiPut<{ suksess: boolean; melding: string; device_idx: number }>(
    '/api/opendaq/device-idx', { device_idx, restart })
