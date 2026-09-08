import { apiGet, apiPut, apiPost } from './client'

export interface InstrumentNett {
  subnett: string
  namn: string
}

export interface InstrumentBru {
  dev: string
  cidr: string
  gateway: string
}

export interface InstrumentNettStatus {
  aktivert: boolean
  nett: InstrumentNett[]
  bru: InstrumentBru | Record<string, never>
  // False = containeren manglar bridge-nettet og maa byggjast paa nytt
  // med "docker compose up -d" paa verten. Rutene kan ikkje leggjast inn
  // foer det er gjort.
  bru_tilgjengeleg: boolean
  aktive_ruter: string[]
  melding?: string
  feil?: string
}

export interface RuteResultat {
  subnett: string
  ok: boolean
  melding: string
}

export const fetchInstrumentNett = () =>
  apiGet<InstrumentNettStatus>('/api/instrumentnett')

export const lagreInstrumentNett = (p: { aktivert: boolean; nett: InstrumentNett[] }) =>
  apiPut<{ suksess: boolean; melding: string; ruter?: RuteResultat[] } & InstrumentNettStatus>(
    '/api/instrumentnett', p)

export const testInstrument = (p: { host: string; port?: number }) =>
  apiPost<{ ok: boolean; melding: string }>('/api/instrumentnett/test', p)
