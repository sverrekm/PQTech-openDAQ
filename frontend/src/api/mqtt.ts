import { apiGet, apiPut, apiPost } from './client'
import type { MqttKonfig, MqttStatus, ActionResult } from './types'

export async function fetchMqttKonfig(): Promise<MqttKonfig> {
  return apiGet<MqttKonfig>('/api/mqtt/konfig')
}

export async function oppdaterMqttKonfig(konfig: MqttKonfig): Promise<ActionResult> {
  return apiPut<ActionResult>('/api/mqtt/konfig', konfig)
}

export async function fetchMqttStatus(): Promise<MqttStatus> {
  return apiGet<MqttStatus>('/api/mqtt/status')
}

// --- Topic-oppdaging (avlytting) ---
export interface MqttForslag {
  json_sti: string
  verdi: number
  kvantitet: string
  eining: string
  interessant: boolean
  namn: string
}
export interface MqttTopicFunn {
  topic: string
  tal: number
  sist_payload: string
  interessant: boolean
  forslag: MqttForslag[]
}
export interface MqttOppdagStatus {
  tilstand: string
  melding: string
  sekund_att: number
  funne: number
  topics: MqttTopicFunn[]
}

export const startMqttOppdag = (varigheit = 15, wildcard = '#') =>
  apiPost<{ suksess: boolean; melding: string }>('/api/mqtt/oppdag/start', { varigheit, wildcard })
export const fetchMqttOppdag = () => apiGet<MqttOppdagStatus>('/api/mqtt/oppdag')
export const stoppMqttOppdag = () => apiPost<{ suksess: boolean; melding: string }>('/api/mqtt/oppdag/stopp')
export const leggTilMqttKanal = (v: {
  topic: string; json_sti: string; namn: string; eining: string; range_min?: number; range_max?: number
}) => apiPost<{ suksess: boolean; melding: string; namn?: string }>('/api/mqtt/oppdag/legg-til', v)
