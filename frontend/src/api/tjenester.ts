import { apiGet, apiPut } from './client'

// --- SMTP-mottakar ---
export interface SmtpKonfig {
  aktivert: boolean
  port: number
  krev_auth: boolean
  brukar: string
  passord_sett?: boolean
  maalkatalog: string
  trekk_kanalar: boolean
  berre_privat: boolean
  status?: {
    tilstand: string; port: number; melding: string; motteke: number
    sist_ts: number | null; sist_frå: string; sist_emne: string
  }
}
export interface SmtpMelding {
  ts: number; frå: string; frå_ip: string; emne: string
  vedlegg: { namn: string; bytes: number }[]; eml: string
}

export const fetchSmtp = () => apiGet<SmtpKonfig>('/api/smtp')
export const lagreSmtp = (k: Partial<SmtpKonfig> & { passord?: string }) =>
  apiPut<{ suksess: boolean; melding: string } & SmtpKonfig>('/api/smtp', k)
export const fetchSmtpMeldingar = () =>
  apiGet<{ meldingar: SmtpMelding[] }>('/api/smtp/meldingar')

// --- NTP/SNTP-tidsserver ---
export interface NtpKonfig {
  aktivert: boolean
  port: number
  stratum: number
  berre_privat: boolean
  status?: {
    tilstand: string; port: number; melding: string; svar: number
    sist_ts: number | null; sist_klient: string
  }
}
export const fetchNtp = () => apiGet<NtpKonfig>('/api/ntp')
export const lagreNtp = (k: Partial<NtpKonfig>) =>
  apiPut<{ suksess: boolean; melding: string } & NtpKonfig>('/api/ntp', k)

// --- FTP-proxy (instrument-FTP over Tailscale) ---
export interface FtpProxyKonfig {
  aktivert: boolean
  lytt_port: number
  maal_vert: string
  maal_port: number
  berre_tailscale: boolean
  bind_ip: string
  maal_vert_effektiv?: string
  tailscale_ip?: string
  lokale_ip?: string[]
  status?: {
    tilstand: string; melding: string; lytt: string; maal: string
    aktive_okter: number; totalt_okter: number
  }
}
export const fetchFtpProxy = () => apiGet<FtpProxyKonfig>('/api/ftp-proxy')
export const lagreFtpProxy = (k: Partial<FtpProxyKonfig>) =>
  apiPut<{ suksess: boolean; melding: string } & FtpProxyKonfig>('/api/ftp-proxy', k)
