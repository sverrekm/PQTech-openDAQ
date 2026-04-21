import { apiGet, apiPut, apiPost, apiDelete } from './client'
import type {
  HubStatus, HubKonfig, HubNodeResult, HubKanal, HubKanalRangeOverstyring,
  ActionResult, NodeType, ModbusRegister, ModbusTestResponse,
} from './types'

export async function fetchHubStatus(): Promise<HubStatus> {
  return apiGet<HubStatus>('/api/hub/status')
}

export async function fetchHubKonfig(): Promise<HubKonfig> {
  return apiGet<HubKonfig>('/api/hub/konfig')
}

export async function oppdaterHubKonfig(konfig: HubKonfig): Promise<ActionResult> {
  return apiPut<ActionResult>('/api/hub/konfig', konfig)
}

export async function leggTilNode(data: {
  adresse: string
  namn?: string
  port?: number
  protokoll?: string
  lokasjon?: string
  type?: NodeType
  modbus_unit_id?: number
  modbus_poll_hz?: number
  modbus_timeout_ms?: number
  modbus_base_adresse?: number
  modbus_registers?: ModbusRegister[]
}): Promise<HubNodeResult> {
  return apiPost<HubNodeResult>('/api/hub/nodar', data)
}

export async function testModbusTilkobling(data: {
  host: string
  port: number
  unit_id: number
  timeout_ms: number
  base_adresse?: number
  registers: ModbusRegister[]
}): Promise<ModbusTestResponse> {
  return apiPost<ModbusTestResponse>('/api/modbus/test', data)
}

export async function fjernNode(nodeId: string): Promise<ActionResult> {
  return apiDelete<ActionResult>(`/api/hub/nodar/${nodeId}`)
}

export async function rekobleNode(nodeId: string): Promise<ActionResult> {
  return apiPost<ActionResult>(`/api/hub/nodar/${nodeId}/rekoble`)
}

export async function fetchHubKanalar(): Promise<{ kanalar: HubKanal[] }> {
  return apiGet<{ kanalar: HubKanal[] }>('/api/hub/kanalar')
}

export async function fetchKanalRanges(): Promise<{ overstyringer: HubKanalRangeOverstyring[] }> {
  return apiGet<{ overstyringer: HubKanalRangeOverstyring[] }>('/api/hub/kanal-ranges')
}

export async function oppdaterKanalRanges(overstyringer: HubKanalRangeOverstyring[]): Promise<ActionResult> {
  return apiPut<ActionResult>('/api/hub/kanal-ranges', { overstyringer })
}

export async function byttModus(modus: string): Promise<ActionResult> {
  return apiPost<ActionResult>('/api/modus/bytt', { modus })
}
