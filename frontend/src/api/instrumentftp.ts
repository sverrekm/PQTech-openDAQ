import { apiGet, apiPut, apiPost } from './client'

/**
 * FTP-henting frå instrument som ikkje strøymer.
 *
 * Elspec G4500 gir ikkje måledata over nettet — arkivet ligg på FTP-en
 * hans, og noden hentar det med jamne mellomrom.
 */
export interface FtpSynkStatus {
  /** '' | 'koeyrer' | 'ok' | 'feil' */
  tilstand: string
  melding: string
  sist_forsok: number | null
  sist_ok: number | null
  nye_sist: number
  totalt_henta: number
  neste_om_s: number | null
  henta_kjende: number
}

export interface FtpKonfig {
  aktivert: boolean
  vert: string
  port: number
  brukar: string
  /** Passordet følgjer aldri med ut — berre om det er sett */
  passord_sett: boolean
  rot: string
  intervall_min: number
  maalkatalog: string
  monster: string
  kanal_prefiks: string
  status?: FtpSynkStatus
}

export interface FtpKanalar {
  kanalar: Record<string, number>
  detaljar: Record<string, { fil: string; tid: string; kanalar: number }>
  feil?: string
}

export interface FtpOppforing {
  namn: string
  storleik: number
  dato: string
  katalog: boolean
  raa: string
  ukjend_format?: boolean
}

export interface FtpListe {
  suksess: boolean
  melding?: string
  sti?: string
  velkomst?: string
  syst?: string
  oppforingar: FtpOppforing[]
}

export interface FtpFil {
  suksess: boolean
  melding?: string
  sti?: string
  storleik?: number | null
  lest?: number
  hex?: string
  tekst?: string
}

export const fetchFtp = () => apiGet<FtpKonfig>('/api/instrument-ftp')

export const lagreFtp = (k: Partial<FtpKonfig> & { passord?: string }) =>
  apiPut<{ suksess: boolean; melding: string } & FtpKonfig>(
    '/api/instrument-ftp', k)

export const testFtp = (vert?: string) =>
  apiPost<{ suksess: boolean; melding?: string; velkomst?: string; syst?: string }>(
    '/api/instrument-ftp/test', { vert })

export const listeFtp = (sti?: string, vert?: string) =>
  apiPost<FtpListe>('/api/instrument-ftp/liste', { sti, vert })

export const filFtp = (sti: string, vert?: string) =>
  apiPost<FtpFil>('/api/instrument-ftp/fil', { sti, vert })

export const synkFtp = () =>
  apiPost<{ suksess: boolean; melding: string }>('/api/instrument-ftp/synk')

export const kanalarFtp = () =>
  apiGet<FtpKanalar>('/api/instrument-ftp/kanalar')
