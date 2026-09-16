import { apiGet, apiPut } from './client'

/**
 * SFTP-innboks: instrument (t.d. PQube 3) som ikkje har FTP-server men kan
 * PUSHE filene sine hit via SFTP. Noden tek imot og arkiverer + parser CSV.
 */
export interface InnboksStatus {
  tilstand: string
  melding: string
  mottatt_totalt: number
  sist_fil: string
  sist_ts: number | null
  kjorer: boolean
}

export interface InnboksKonfig {
  aktivert: boolean
  brukar: string
  passord: string
  kunde: string
  kanal_prefiks: string
  vert: string          // LAN-IP instrumentet skal pushe til
  port: number
  fjern_sti: string     // fjern-katalog på instrumentet ("/opplasting")
  status?: InnboksStatus
}

export const fetchInnboks = () => apiGet<InnboksKonfig>('/api/innboks')

export const lagreInnboks = (
  k: Partial<Pick<InnboksKonfig, 'aktivert' | 'kunde' | 'kanal_prefiks'>>
  & { regenerer_passord?: boolean },
) => apiPut<{ suksess: boolean; melding: string } & InnboksKonfig>('/api/innboks', k)
