import { apiGet, apiPost } from './client'

export interface WifiNett {
  ssid: string
  signal: number
  sikring: string
  open: boolean
  aktiv: boolean
}

export interface WifiStatus {
  nmcli_tilgjengeleg: boolean
  radio: boolean | null
  device: string
  tilkobla: boolean
  ssid: string
  signal: number | null
  ip: string
  tilstand: string
  feil?: string
}

export interface WifiSkann {
  suksess: boolean
  nett?: WifiNett[]
  melding?: string
}

export const fetchWifiStatus = () => apiGet<WifiStatus>('/api/wifi/status')

export const skannWifi = () => apiPost<WifiSkann>('/api/wifi/skann')

export const kobleWifi = (p: { ssid: string; passord?: string; skjult?: boolean }) =>
  apiPost<{ suksess: boolean; melding: string } & WifiStatus>('/api/wifi/koble', p)

export const gloymWifi = (ssid: string) =>
  apiPost<{ suksess: boolean; melding: string } & WifiStatus>('/api/wifi/gloym', { ssid })
