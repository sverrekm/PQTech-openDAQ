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
  // Profilnamnet i NetworkManager. Ikkje det same som ssid: Pi Imager lagar
  // "preconfigured", og finst profilen frå før blir det "SSID 1".
  profil?: string
  // Tilkopling er treg og køyrer i bakgrunnen paa noden; her ser vi korleis
  // det gaar. tilstand: '' | 'koeyrer' | 'ok' | 'feil'
  siste_op?: {
    tilstand: string
    ssid: string
    melding: string
    alder_s: number | null
  }
}

export interface WifiSkann {
  suksess: boolean
  nett?: WifiNett[]
  melding?: string
}

export const fetchWifiStatus = () => apiGet<WifiStatus>('/api/wifi/status')

export const skannWifi = () => apiPost<WifiSkann>('/api/wifi/skann')

// berre_lokalt: instrument-nett (Elspec BlackBox o.l.) som ikkje skal få
// vere default-rute eller DNS-kjelde. La den vere av når wifi-et ER vegen ut.
export const kobleWifi = (p: {
  ssid: string
  passord?: string
  skjult?: boolean
  berre_lokalt?: boolean
  // Fast IP i CIDR, t.d. 192.168.50.20/24. Gaar utanom DHCP - naudsynt naar
  // instrument-ruteren ikkje deler ut leige (NM blir staaande i tilstand 70).
  statisk_ip?: string
  gateway?: string
}) =>
  apiPost<{ suksess: boolean; melding: string } & WifiStatus>('/api/wifi/koble', p)

export const gloymWifi = (ssid: string) =>
  apiPost<{ suksess: boolean; melding: string } & WifiStatus>('/api/wifi/gloym', { ssid })
