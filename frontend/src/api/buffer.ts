import { apiGet, apiPut } from './client'
import type { BufferStatus, BufferKonfig, HubBufferStatus, ActionResult, BufferHending, MqttLoggRad, LagringsInfo } from './types'

export const fetchBufferStatus = () =>
  apiGet<BufferStatus>('/api/buffer/status')

export const fetchBufferKonfig = () =>
  apiGet<BufferKonfig>('/api/buffer/konfig')

export const oppdaterBufferKonfig = (konfig: BufferKonfig) =>
  apiPut<ActionResult>('/api/buffer/konfig', konfig)

export const fetchHubBufferStatus = () =>
  apiGet<HubBufferStatus>('/api/hub/buffer/status')

export const fetchBufferHendingar = (limit = 50, etterId = 0) =>
  apiGet<{ hendingar: BufferHending[] }>(`/api/buffer/hendingar?limit=${limit}&etter_id=${etterId}`)

export const fetchBufferMqttLogg = (limit = 100, etterTid = 0) =>
  apiGet<{ rader: MqttLoggRad[] }>(`/api/buffer/mqtt-logg?limit=${limit}&etter_tid=${etterTid}`)

export const fetchBufferLagring = () =>
  apiGet<LagringsInfo>('/api/buffer/lagring')
