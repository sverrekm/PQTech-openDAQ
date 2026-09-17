import { apiGet, apiPut, apiPost } from './client'

/**
 * HTTP-henting av PQube 3 event-tre: noden blar PQubens web (katalog-lister)
 * og drar ned event-filer (PQDIF/CSV/bølgeform) til arkivet. Meir påliteleg
 * enn FTP-push — får med både eksisterande og nye hendingar.
 */
export interface PqubeHentStatus {
  tilstand: string
  melding: string
  henta_totalt: number
  sist_fil: string
  sist_ts: number | null
  nye_sist: number
  kjorer: boolean
  henta_kjende: number
}

export interface PqubeHentKonfig {
  aktivert: boolean
  vert: string
  port: number
  brukar: string
  passord_sett: boolean
  intervall_min: number
  dagar_tilbake: number
  kunde: string
  kanal_prefiks: string
  hent_gif: boolean
  status?: PqubeHentStatus
}

export const fetchPqubeHent = () => apiGet<PqubeHentKonfig>('/api/pqube-hent')

export const lagrePqubeHent = (k: Partial<PqubeHentKonfig> & { passord?: string }) =>
  apiPut<{ suksess: boolean; melding: string } & PqubeHentKonfig>('/api/pqube-hent', k)

export const hentPqubeNo = () =>
  apiPost<{ suksess: boolean; melding: string }>('/api/pqube-hent/hent-no')
