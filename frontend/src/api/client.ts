const BASE = ''

let _bekreftar401 = false

function _reloadEinGong() {
  // Loop-vakt: ikkje reload oftare enn éin gong per 8 s.
  try {
    const no = Date.now()
    const sist = Number(sessionStorage.getItem('siste_401_reload') || '0')
    if (no - sist < 8000) return
    sessionStorage.setItem('siste_401_reload', String(no))
  } catch {
    /* sessionStorage utilgjengeleg — reload likevel */
  }
  window.location.reload()
}

function handle401(res: Response) {
  if (res.status !== 401) return
  // Over ein flaky link (5G/CGNAT via Tailscale-relay) kan hub-proxyen svare
  // 401 sporadisk sjølv om vi framleis er innlogga. Ei enkeltståande 401 skal
  // difor IKKJE kaste brukaren ut — vi stadfestar mot /api/auth/status og
  // lastar berre på nytt viss sesjonen faktisk er borte. Timeout/nettfeil/5xx
  // under stadfestinga tel som «uklart» → vi blir verande innlogga.
  if (_bekreftar401) return
  _bekreftar401 = true
  fetch(`${BASE}/api/auth/status`, { credentials: 'include' })
    .then(async r => {
      if (r.ok) {
        const d = await r.json().catch(() => null)
        if (d && d.innlogga) return          // framleis innlogga — transient 401
      } else if (r.status !== 401) {
        return                                // uklart svar (5xx/502) — ikkje ut
      }
      _reloadEinGong()                        // stadfesta utlogga
    })
    .catch(() => { /* nettfeil under stadfesting → ikkje kast ut */ })
    .finally(() => { _bekreftar401 = false })
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
