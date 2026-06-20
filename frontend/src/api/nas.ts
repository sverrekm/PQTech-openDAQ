import { apiGet, apiPost } from './client'

export interface NasShare { ip: string; hostname: string; shares: string[] }
export interface NasOppdag { suksess: boolean; subnet?: string; vertar?: NasShare[]; melding?: string }
export interface NasStatus {
  montert: boolean
  mountpunkt: string
  server: string
  share: string
  ledig_gb?: number | null
  total_gb?: number | null
  skrivbar?: boolean
  passord_satt?: boolean
  brukar?: string
  melding?: string
}

export const oppdagNas = (brukar = '', passord = '') =>
  apiPost<NasOppdag>('/api/nas/oppdag', { brukar, passord })

export const monterNas = (p: {
  server: string; share: string; brukar?: string; passord?: string
  mountpunkt?: string; domene?: string
}) => apiPost<{ suksess: boolean; melding: string } & NasStatus>('/api/nas/monter', p)

export const avmonterNas = () =>
  apiPost<{ suksess: boolean; melding: string } & NasStatus>('/api/nas/avmonter')

export const fetchNasStatus = () => apiGet<NasStatus>('/api/nas/status')
