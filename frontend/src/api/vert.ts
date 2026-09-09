import { apiGet, apiPost } from './client'

export interface VertGrensesnitt {
  dev: string
  /** CIDR om det har adresse, elles tom */
  adresse: string
  oppe: boolean
  /** 'kabel' | 'wifi' */
  type: string
}

export interface ByggOmStatus {
  /** Compose-prosjektet si mappe paa verten */
  repo: string
  /** False = ingen docker-CLI paa verten (nokre nodar koeyrer under containerd) */
  docker: boolean
  docker_versjon: string
  compose_har_instrumentnett: boolean
  /** Verdiar vi fyller inn i .env foer ombygging */
  manglar: Record<string, string>
  /** False = vi kan ikkje stadfeste at containeren kjem opp att */
  trygt: boolean
  grunn: string
  grensesnitt: string
  siste_utdata: string
  feil?: string
}

export const fetchGrensesnitt = () =>
  apiGet<{ grensesnitt: VertGrensesnitt[] }>('/api/vert/grensesnitt')

export const fetchByggOm = () => apiGet<ByggOmStatus>('/api/vert/bygg-om')

export const byggOm = () =>
  apiPost<{ suksess: boolean; melding: string }>('/api/vert/bygg-om')
