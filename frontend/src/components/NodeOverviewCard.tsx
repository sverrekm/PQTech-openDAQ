import { useCallback } from 'react'
import { fetchHubStatus } from '../api/hub'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'
import type { HubNodeStatus } from '../api/types'

/** Node-oversikt for hub-modus: status per tilkopla måleboks (OK/Manglar),
 *  kanaltal, sist sett, og lenke til node-UI for openDAQ-nodar. */
export default function NodeOverviewCard() {
  const { t, lang } = useI18n()
  const fetcher = useCallback(() => fetchHubStatus(), [])
  const { data: hub, loading } = usePolling(fetcher, 5000)
  const locale = lang === 'nb' ? 'nb-NO' : 'en-US'

  if (loading) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm flex justify-center items-center h-32">
        <svg className="animate-spin h-8 w-8 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

  const nodar = hub?.nodar ?? []

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold">{t('Nodes')}</h2>
        <span className="text-sm text-gray-500">
          {hub?.tilkobla_nodar ?? 0}/{hub?.totalt_nodar ?? nodar.length} {t('connected')}
        </span>
      </div>
      <p className="text-sm text-gray-500 -mt-0.5 mb-4 leading-snug">{t('Measurement boxes this hub collects data from, with live status for each.')}</p>

      {nodar.length === 0 ? (
        <div className="text-sm text-gray-400 py-4 text-center">{t('No nodes configured yet')}</div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
          {nodar.map(n => <NodeTile key={n.id} node={n} locale={locale} t={t} />)}
        </div>
      )}
    </div>
  )
}

function NodeTile({ node, locale, t }: { node: HubNodeStatus; locale: string; t: (k: string) => string }) {
  const erOpenDaq = !node.type || node.type === 'opendaq'
  const status = !node.aktivert ? 'av' : node.tilkobla ? 'ok' : 'manglar'
  const dot = status === 'ok' ? 'bg-green-500' : status === 'av' ? 'bg-gray-400' : 'bg-red-500'
  const statusTekst = status === 'ok' ? t('OK') : status === 'av' ? t('Disabled') : t('Missing')
  const statusFarge = status === 'ok' ? 'text-green-700' : status === 'av' ? 'text-gray-500' : 'text-red-700'
  const sistSett = node.sist_sett ? new Date(node.sist_sett).toLocaleString(locale) : null

  const innhald = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 min-w-0">
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dot}`} />
          <span className="font-semibold text-gray-900 truncate">{node.namn}</span>
        </span>
        <span className={`text-xs font-medium flex-shrink-0 ${statusFarge}`}>{statusTekst}</span>
      </div>
      <div className="mt-1 flex items-center gap-2 text-xs text-gray-500">
        <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
          {erOpenDaq ? 'openDAQ' : t('Modbus TCP')}
        </span>
        {node.lokasjon && <span className="truncate">{node.lokasjon}</span>}
      </div>
      <div className="mt-1 text-xs text-gray-500">
        {node.tilkobla
          ? `${node.antal_kanalar} ${t('channels')}`
          : sistSett ? `${t('Last seen')}: ${sistSett}` : '—'}
      </div>
      {node.feil && <div className="mt-1 text-xs text-red-600 truncate" title={node.feil}>{node.feil}</div>}
    </>
  )

  // openDAQ-nodar har web-UI på :8080 — gjer heile flisa til ei lenke.
  if (erOpenDaq) {
    return (
      <a
        href={`/node-proxy/${node.id}/`}
        target="_blank"
        rel="noopener noreferrer"
        title={t('Open UI')}
        className="group block border border-gray-200 rounded-lg p-3 hover:border-[#D76428] hover:bg-orange-50/30 transition-colors"
      >
        {innhald}
        <div className="mt-1 text-xs text-[#D76428] opacity-0 group-hover:opacity-100 transition-opacity">{t('Open UI')} ↗</div>
      </a>
    )
  }
  return <div className="border border-gray-200 rounded-lg p-3">{innhald}</div>
}
