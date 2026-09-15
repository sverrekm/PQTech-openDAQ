import { useState, useEffect, useCallback } from 'react'
import { fetchPushKonfig, oppdaterPushKonfig, fetchPushStatus } from '../api/push'
import type { PushKonfig, PushStatus } from '../api/push'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'
import Panel from './ui/Panel'

/** Hub-tilkobling: kvar denne noden pushar måledata (parent-URL + token) og
 *  live push-status. Lèt ein fjern-node (berre nåbar via Tailscale/hub-proxy)
 *  konfigurerast utan SSH — og viser med ein gong om pushen faktisk går. */
export default function HubConnectionCard() {
  const { t } = useI18n()
  const [konfig, setKonfig] = useState<PushKonfig | null>(null)
  const [parentUrl, setParentUrl] = useState('')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => {
    fetchPushKonfig().then(k => {
      setKonfig(k)
      setParentUrl(k.parent_url || '')
      setToken(k.parent_token || '')
    }).catch(() => {})
  }, [])

  const statusFetcher = useCallback(() => fetchPushStatus(), [])
  const { data: status } = usePolling<PushStatus>(statusFetcher, 5000)

  const lagre = async () => {
    if (!konfig) return
    setBusy(true); setMelding(null)
    try {
      const r = await oppdaterPushKonfig({
        ...konfig,
        parent_url: parentUrl.trim(),
        parent_token: token.trim(),
      })
      setMelding({ text: r.melding || t('Saved.'), ok: r.suksess })
    } catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy(false)
  }

  // Push-tilstand → farge + tekst.
  const tilstand = (() => {
    if (!status) return { farge: 'bg-gray-100 text-gray-600', tekst: t('Loading…') }
    if (!status.konfigurert) return { farge: 'bg-gray-100 text-gray-600', tekst: t('Not pushing (no hub URL)') }
    if (!status.kjorer) return { farge: 'bg-yellow-100 text-yellow-800', tekst: t('Configured, not running') }
    if (status.siste_feilmelding || (status.siste_status_kode && status.siste_status_kode >= 400))
      return { farge: 'bg-red-100 text-red-800', tekst: t('Push error') }
    if ((status.sendt_ok ?? 0) > 0)
      return { farge: 'bg-green-100 text-green-800', tekst: t('Pushing OK') }
    return { farge: 'bg-yellow-100 text-yellow-800', tekst: t('Starting…') }
  })()

  const sidenSend = status?.siste_send_ts
    ? `${Math.max(0, Math.round(Date.now() / 1000 - status.siste_send_ts))} s`
    : '—'

  return (
    <Panel
      kicker={t('Connection')}
      title={t('Hub connection')}
      sub={t('Where this node pushes its channels (the hub). Set the hub URL and the shared fleet token, then watch that the push actually reaches the hub.')}
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="ui-label">{t('Hub URL')}</label>
          <input
            type="text" className="ui-input mono"
            value={parentUrl} onChange={e => setParentUrl(e.target.value)}
            placeholder="https://opendac.pqtech.no"
          />
        </div>
        <div>
          <label className="ui-label">{t('Fleet token')}</label>
          <input
            type="password" className="ui-input mono" autoComplete="off"
            value={token} onChange={e => setToken(e.target.value)}
            placeholder={t('shared node ↔ hub token')}
          />
        </div>
      </div>

      <div className="flex items-center gap-3 mt-3">
        <button onClick={lagre} disabled={busy || !konfig} className="btn-primary">
          {busy ? t('Saving...') : t('Save')}
        </button>
        <span className={`inline-block px-2.5 py-1 rounded text-xs font-medium ${tilstand.farge}`}>
          {tilstand.tekst}
        </span>
      </div>

      {/* Live push-status */}
      <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
        <div className="meta-panel">
          <div className="ui-label">{t('Last send')}</div>
          <div className="mono">{sidenSend}</div>
        </div>
        <div className="meta-panel">
          <div className="ui-label">{t('Latency')}</div>
          <div className="mono">{status?.siste_latens_ms != null ? `${status.siste_latens_ms} ms` : '—'}</div>
        </div>
        <div className="meta-panel">
          <div className="ui-label">{t('Sent OK')}</div>
          <div className="mono">{status?.sendt_ok ?? '—'}</div>
        </div>
        <div className="meta-panel">
          <div className="ui-label">{t('Failed')}</div>
          <div className="mono">{status?.sendt_feil ?? '—'}</div>
        </div>
      </div>

      {/* Aktuell feil: berre synleg når SISTE send faktisk feila (backend
          nullstiller meldinga ved kvar vellukka send). */}
      {status?.siste_feilmelding && (
        <div className="mt-2 px-3 py-2 rounded text-sm bg-red-50 text-red-800 border border-red-200">
          {t('Last send failed')}: {status.siste_status_kode ? `[${status.siste_status_kode}] ` : ''}{status.siste_feilmelding}
        </div>
      )}

      {/* Sunn push, men nokre historiske blipp (t.d. forbigåande 502 frå CDN).
          Beroligar: dette er ikkje eit aktivt problem. */}
      {status && !status.siste_feilmelding && (status.sendt_feil ?? 0) > 0 && (
        <div className="mt-2 text-xs text-gray-400">
          {t('{n} transient errors since start — auto-recovered, push is healthy.').replace('{n}', String(status.sendt_feil))}
        </div>
      )}

      {melding && (
        <div className={`mt-3 px-3 py-2 rounded text-sm ${melding.ok ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-red-50 text-red-800 border border-red-200'}`}>
          {melding.text}
        </div>
      )}
    </Panel>
  )
}
