import { apiGet, apiPost } from './client'

export interface SkannPort {
  port: number
  namn: string
  /** Gir ein faktisk dataveg (Modbus, OPC UA, MQTT, openDAQ, SSH ...) */
  interessant?: boolean
}

export interface SkannFunn {
  ip: string
  portar: SkannPort[]
  /** 'TCP' = svarte på ein port, 'ICMP' = berre ping */
  svar?: string
  server?: string
  tittel?: string
}

export interface SkannMaal {
  namn: string
  subnett: string
  /** Tomt naar nettet naaast via rutinga (t.d. eit NAT-alias) */
  grensesnitt: string
  kan_binde: boolean
}

export interface SkannStatus {
  /** '' | 'koeyrer' | 'ferdig' | 'stoppa' | 'feil' */
  tilstand: string
  subnett: string
  grensesnitt: string
  ferdig: number
  totalt: number
  funn: SkannFunn[]
  melding: string
  brukt_s: number | null
}

export const fetchSkann = () => apiGet<SkannStatus>('/api/nettskann')

export const startSkann = (subnett: string, grensesnitt?: string) =>
  apiPost<{ suksess: boolean; melding: string } & SkannStatus>(
    '/api/nettskann/start', { subnett, grensesnitt })

/** Nett det gir meining aa skanne, med grensesnittet dei ligg bak. */
export const fetchSkannMaal = () =>
  apiGet<{ maal: SkannMaal[] }>('/api/nettskann/maal')

export const stoppSkann = () =>
  apiPost<{ suksess: boolean; melding: string }>('/api/nettskann/stopp')

export interface SisteFunn {
  tid: number
  funn: SkannFunn[]
}

/** Siste kjende funn per subnett, frå autoskannet. */
export const fetchSisteSkann = () =>
  apiGet<{ subnett: Record<string, SisteFunn> }>('/api/nettskann/siste')
