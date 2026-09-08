import { apiGet, apiPut, apiPost } from './client'

/**
 * Instrument-NAT: noden som ruter mellom LAN og instrumentnett.
 *
 * Instrumentet held sin faste adresse (Elspec staar alltid paa
 * 192.168.1.x og gaar tilbake dit ved reset). Containeren snakkar med
 * ALIAS-adressa i staden, og verten mapper 1:1. Dei to nettverka moetest
 * aldri, so like nettmasker er uproblematisk.
 */
export interface NatNett {
  namn: string
  /** Grensesnittet instrumentnettet ligg paa, typisk wlan0 */
  grensesnitt: string
  /** Instrumentet si verkelege adressering, t.d. 192.168.1.0/24 */
  ekte: string
  /** Adressa vi brukar i staden, t.d. 10.99.0.0/24. Same prefikslengd. */
  alias: string
  tabell: number
  merke: number
  aktiv?: boolean
  bord?: string[]
}

export interface NatStatus {
  aktivert: boolean
  nett: NatNett[]
  /** False = kom ikkje til vertens nettverksoppsett i det heile */
  vert_ok: boolean
  feil?: string
}

export const fetchNat = () => apiGet<NatStatus>('/api/instrument-nat')

export const lagreNat = (p: { aktivert: boolean; nett: NatNett[] }) =>
  apiPut<{ suksess: boolean; melding: string } & NatStatus>('/api/instrument-nat', p)

export const testNat = (p: { adresse: string; port?: number }) =>
  apiPost<{ ok: boolean; melding: string }>('/api/instrument-nat/test', p)
