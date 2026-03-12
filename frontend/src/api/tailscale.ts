import { apiGet, apiPost } from './client'
import type { TailscaleStatus, ActionResult } from './types'

export async function fetchTailscaleStatus(): Promise<TailscaleStatus> {
  return apiGet<TailscaleStatus>('/api/tailscale/status')
}

export async function startTailscale(data: { authkey: string; hostname?: string }): Promise<ActionResult> {
  return apiPost<ActionResult>('/api/tailscale/start', data)
}

export async function stoppTailscale(): Promise<ActionResult> {
  return apiPost<ActionResult>('/api/tailscale/stopp')
}
