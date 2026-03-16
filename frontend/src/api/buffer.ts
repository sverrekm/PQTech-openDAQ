import { apiGet, apiPut } from './client'
import type { BufferStatus, BufferKonfig, HubBufferStatus, ActionResult } from './types'

export const fetchBufferStatus = () =>
  apiGet<BufferStatus>('/api/buffer/status')

export const fetchBufferKonfig = () =>
  apiGet<BufferKonfig>('/api/buffer/konfig')

export const oppdaterBufferKonfig = (konfig: BufferKonfig) =>
  apiPut<ActionResult>('/api/buffer/konfig', konfig)

export const fetchHubBufferStatus = () =>
  apiGet<HubBufferStatus>('/api/hub/buffer/status')
