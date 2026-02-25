import { apiGet, apiPut } from './client'
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
