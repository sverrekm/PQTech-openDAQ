import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchHubLogging, setNodeLogging } from '../api/logging'
import type { HubLogging, LoggModus, NodeLoggInnstilling } from '../api/logging'
import { useI18n } from '../i18n'
import { useToast } from './Toast'
import Panel from './ui/Panel'

/** epoch ms -> verdi for <input type="datetime-local"> (lokal tid). */
function msTilInput(ms: number): string {
  if (!ms) return ''
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
const inputTilMs = (s: string): number => (s ? new Date(s).getTime() : 0)

/**
 * Per-node logge-styring på hubben. Bestemmer om ein node sine måledata vert
 * LAGRA (hub_lager + NAS). Live-dashbordet er ikkje råka — du ser framleis at
 * noden lever. Nyttig når ein node skal stengast/flyttast, eller når du berre
 * vil logge i eit bestemt tidsvindauge.
 */
export default function HubLoggingCard() {
  const { t } = useI18n()
  const toast = useToast()
  const fetcher = useCallback(() => fetchHubLogging(), [])
  const { data, refresh } = usePolling<HubLogging>(fetcher, 5000)

  // Lokalt utkast for planlagt-tidene per node (før lagring).
  const [utkast, setUtkast] = useState<Record<string, { start: string; slutt: string }>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const innst = (namn: string): NodeLoggInnstilling =>
    data?.innstillingar[namn] || { modus: 'kontinuerleg', start_ms: 0, slutt_ms: 0 }

  const lagre = async (namn: string, modus: LoggModus, start_ms = 0, slutt_ms = 0) => {
    setBusy(namn)
    try {
      const r = await setNodeLogging(namn, modus, start_ms, slutt_ms)
      if (r.suksess) toast.suksess(r.melding)
      else toast.feil(r.melding)
      refresh()
    } catch (e) {
      toast.feil(`${t('Error')}: ${e}`)
    }
    setBusy(null)
  }

  const nodar = data?.nodar || []

  return (
    <Panel
      kicker={t('Storage')}
      title={t('Per-node logging')}
      sub={t('Control whether each node’s data is stored on the hub. Live view is unaffected — pausing only stops writing to disk (hub database + NAS). Useful when a node is being shut down or moved.')}
    >
      {nodar.length === 0 ? (
        <p className="text-sm text-gray-500 py-3">{t('No nodes configured.')}</p>
      ) : (
        <div className="space-y-3">
          {nodar.map(node => {
            const i = innst(node.namn)
            const u = utkast[node.namn] || {
              start: msTilInput(i.start_ms), slutt: msTilInput(i.slutt_ms),
            }
            const settUtkast = (felt: 'start' | 'slutt', v: string) =>
              setUtkast(prev => ({ ...prev, [node.namn]: { ...u, [felt]: v } }))
            const knapp = (m: LoggModus, tekst: string) => (
              <button
                onClick={() => lagre(node.namn, m,
                  m === 'planlagt' ? inputTilMs(u.start) : 0,
                  m === 'planlagt' ? inputTilMs(u.slutt) : 0)}
                disabled={busy === node.namn}
                className={`text-xs px-3 py-1.5 rounded border transition-colors disabled:opacity-50 ${
                  i.modus === m
                    ? 'bg-[#D76428] text-white border-[#D76428]'
                    : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-100'
                }`}
              >{tekst}</button>
            )
            return (
              <div key={node.id} className="border border-gray-200 rounded-lg p-3">
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                      node.loggar_no ? 'bg-green-500' : 'bg-gray-400'}`} />
                    <span className="text-sm font-medium text-gray-900 truncate">{node.namn}</span>
                    <span className={`text-xs ${node.loggar_no ? 'text-green-600' : 'text-gray-500'}`}>
                      {node.loggar_no ? t('Logging now') : t('Not logging')}
                    </span>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {knapp('kontinuerleg', t('Continuous'))}
                  {knapp('av', t('Paused'))}
                  {knapp('planlagt', t('Scheduled'))}
                </div>
                {i.modus === 'planlagt' && (
                  <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="ui-label">{t('Start (blank = now)')}</label>
                      <input type="datetime-local" className="ui-input"
                        value={u.start} onChange={e => settUtkast('start', e.target.value)} />
                    </div>
                    <div>
                      <label className="ui-label">{t('End (blank = never)')}</label>
                      <input type="datetime-local" className="ui-input"
                        value={u.slutt} onChange={e => settUtkast('slutt', e.target.value)} />
                    </div>
                    <div className="sm:col-span-2">
                      <button
                        onClick={() => lagre(node.namn, 'planlagt', inputTilMs(u.start), inputTilMs(u.slutt))}
                        disabled={busy === node.namn}
                        className="btn-primary text-sm"
                      >{busy === node.namn ? t('Saving...') : t('Save schedule')}</button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}
