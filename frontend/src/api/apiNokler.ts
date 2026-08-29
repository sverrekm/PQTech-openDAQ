import { apiGet, apiPost, apiPut, apiDelete } from './client'

/** API-nøkkel slik han vert vist i GUI-et. Klarteksten finst ikkje her —
 *  han vert returnert éin einaste gong, av opprettApiNokkel(). */
export interface ApiNokkel {
  id: string
  namn: string
  prefiks: string
  oppretta: string
  sist_brukt: string
  aktivert: boolean
  utloep: string
  kanal_filter: string[]
  scope: string
}

export interface NyApiNokkel extends ApiNokkel {
  suksess: boolean
  /** Klartekst — vist éin gong, kan ikkje hentast fram att. */
  nokkel: string
}

export const fetchApiNokler = () =>
  apiGet<{ nokler: ApiNokkel[] }>('/api/api-nokler')

export const opprettApiNokkel = (n: {
  namn: string; utloep?: string; kanal_filter?: string[]
}) => apiPost<NyApiNokkel>('/api/api-nokler', n)

export const settApiNokkelAktivert = (id: string, aktivert: boolean) =>
  apiPut<{ suksess: boolean }>(`/api/api-nokler/${id}`, { aktivert })

export const slettApiNokkel = (id: string) =>
  apiDelete<{ suksess: boolean }>(`/api/api-nokler/${id}`)
