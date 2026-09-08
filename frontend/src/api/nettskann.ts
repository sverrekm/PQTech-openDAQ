import { apiGet, apiPost } from './client'

export interface SkannPort {
  port: number
  namn: string
}

export interface SkannFunn {
  ip: string
  portar: SkannPort[]
  /** 'TCP' = svarte på ein port, 'ICMP' = berre ping */
  svar?: string
  server?: string
  tittel?: string
}

export interface SkannStatus {
  /** '' | 'koeyrer' | 'ferdig' | 'stoppa' | 'feil' */
  tilstand: string
  subnett: string
  ferdig: number
  totalt: number
  funn: SkannFunn[]
  melding: string
  brukt_s: number | null
}

export const fetchSkann = () => apiGet<SkannStatus>('/api/nettskann')

export const startSkann = (subnett: string) =>
  apiPost<{ suksess: boolean; melding: string } & SkannStatus>(
    '/api/nettskann/start', { subnett })

export const stoppSkann = () =>
  apiPost<{ suksess: boolean; melding: string }>('/api/nettskann/stopp')
