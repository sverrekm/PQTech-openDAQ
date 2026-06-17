const BASE = ''

function handle401(res: Response) {
  if (res.status === 401) {
    window.location.reload()
  }
}

// Surface backend-feilmelding (JSON {feil}|{melding}) i staden for berre
// "GET /x: 500". Gir brukaren forklarande tekst når serveren sender ein.
async function kastFeil(res: Response, method: string, path: string): Promise<never> {
  let melding = ''
  try {
    const data = await res.json()
    melding = data?.feil || data?.melding || ''
  } catch {
    /* ikkje JSON-kropp */
  }
  throw new Error(melding || `${method} ${path}: ${res.status}`)
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
  })
  handle401(res)
  if (!res.ok) await kastFeil(res, 'GET', path)
  return res.json()
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : '{}',
    credentials: 'include',
  })
  handle401(res)
  if (!res.ok) await kastFeil(res, 'POST', path)
  return res.json()
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'include',
  })
  handle401(res)
  if (!res.ok) await kastFeil(res, 'PUT', path)
  return res.json()
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  handle401(res)
  if (!res.ok) await kastFeil(res, 'DELETE', path)
  return res.json()
}
