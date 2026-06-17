import { apiGet, apiPut, apiPost } from './client'

export interface InfluxKonfig {
  aktivert: boolean
  url: string
  org: string
  bucket: string
  intervall_s: number
  token_satt: boolean
}

export const fetchInfluxKonfig = () => apiGet<InfluxKonfig>('/api/influx/konfig')

export const lagreInfluxKonfig = (k: {
  aktivert: boolean; url: string; org: string; bucket: string
  intervall_s: number; token?: string
}) => apiPut<{ suksess: boolean } & InfluxKonfig>('/api/influx/konfig', k)

export const testInflux = () =>
  apiPost<{ suksess: boolean; melding: string }>('/api/influx/test')
