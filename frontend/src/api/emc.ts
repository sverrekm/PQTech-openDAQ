import { apiGet, apiPut, apiPost } from './client'

export interface EmcKonfig {
  aktivert: boolean
  intervall_s: number
  nettfrekvens: number
  n_harmoniske: number
  syklusar: number
  fft_maks_hz: number
  fft_bins: number
}

export const fetchEmcKonfig = () => apiGet<EmcKonfig>('/api/emc/konfig')

export const lagreEmcKonfig = (k: Partial<EmcKonfig>) =>
  apiPut<{ suksess: boolean } & EmcKonfig>('/api/emc/konfig', k)

export const testEmc = () =>
  apiPost<{ suksess: boolean; melding: string }>('/api/emc/test')
