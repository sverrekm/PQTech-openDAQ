import type { EnhetKonfig } from './types'

const BASE = '/api/enhet'

export async function fetchEnhetKonfig(): Promise<EnhetKonfig> {
  const res = await fetch(`${BASE}/konfig`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function oppdaterEnhetKonfig(konfig: EnhetKonfig): Promise<{ suksess: boolean; melding: string }> {
  const res = await fetch(`${BASE}/konfig`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(konfig),
  })
  return res.json()
}
