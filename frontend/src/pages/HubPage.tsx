import { useState, useCallback, useEffect } from 'react'
import type { HubStatus, HubKanal, HubKanalRangeOverstyring } from '../api/types'
import { fetchHubStatus, fetchHubKanalar, fetchKanalRanges, oppdaterKanalRanges, byttModus } from '../api/hub'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'
import BufferStatusCard from '../components/BufferStatusCard'

export const HUB_SYNLEGE_KEY = 'hub_synlege_kanalar'

export function hentSynlegeKanalar(): Set<string> {
  try {
    const raw = localStorage.getItem(HUB_SYNLEGE_KEY)
    return raw ? new Set(JSON.parse(raw)) : new Set()
  } catch { return new Set() }
}

function lagreSynlegeKanalar(set: Set<string>) {
  localStorage.setItem(HUB_SYNLEGE_KEY, JSON.stringify([...set]))
}

export default function HubPage() {
  const { t, lang } = useI18n()
  const hubFetcher = useCallback(() => fetchHubStatus(), [])
  const { data: hub, loading } = usePolling(hubFetcher, 3000)

  const [melding, setMelding] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [modusGjenstartar, setModusGjenstartar] = useState(false)

  const handleByttModus = async (nyModus: string) => {
    setActionLoading('modus')
    try {
      const res = await byttModus(nyModus)
      setMelding(res.melding)
      if (res.suksess) {
        setModusGjenstartar(true)
        setTimeout(() => window.location.reload(), 10000)
      }
    } catch (e) {
      setMelding(`${t('Error')}: ${e}`)
    }
    setActionLoading(null)
  }

  if (loading && !hub) {
    return (
      <div className="flex justify-center items-center h-32">
        <svg className="animate-spin h-5 w-5 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

  const hubAktiv = hub?.aktiv !== false

  return (
    <>
      {/* Summary */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-900">openDAQ Hub</h2>
          <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium ${
            hubAktiv ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'
          }`}>
            <span className={`w-2 h-2 rounded-full ${hubAktiv ? 'bg-green-500 animate-pulse' : 'bg-yellow-500'}`} />
            {hubAktiv ? t('Hub active') : t('Hub not active')}
          </div>
        </div>

        {modusGjenstartar ? (
          <div className="bg-blue-50 border border-blue-200 text-blue-800 text-sm rounded-lg p-3 mb-3 flex items-center gap-2">
            <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            {t('Restarting container — page will reload in a few seconds...')}
          </div>
        ) : !hubAktiv ? (
          <div className="bg-yellow-50 border border-yellow-200 text-yellow-800 text-sm rounded-lg p-3 mb-3 flex items-center justify-between">
            <span>{t('Hub service is not running. Enable hub mode to connect to remote nodes.')}</span>
            <button
              onClick={() => handleByttModus('hub')}
              disabled={actionLoading === 'modus'}
              className="ml-3 flex-shrink-0 text-xs font-medium px-3 py-1.5 rounded-md bg-[#D76428] text-white hover:bg-[#c55a23] disabled:opacity-50 transition-colors"
            >
              {actionLoading === 'modus' ? t('Switching...') : t('Enable Hub mode')}
            </button>
          </div>
        ) : (
          <div className="flex justify-end mb-3">
            <button
              onClick={() => handleByttModus('direkte')}
              disabled={actionLoading === 'modus'}
              className="text-xs font-medium px-3 py-1.5 rounded-md border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
            >
              {actionLoading === 'modus' ? t('Switching...') : t('Disable Hub mode')}
            </button>
          </div>
        )}

        <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-2">
          <InfoCell label={t('Connected nodes')} value={`${hub?.tilkobla_nodar ?? 0} / ${hub?.totalt_nodar ?? 0}`} />
          <InfoCell label={t('Total channels')} value={String(hub?.totalt_kanalar ?? 0)} />
          <InfoCell label={t('Hub IP')} value={hub?.ip ?? '-'} />
          <InfoCell label="OPC-UA" value={hub?.ip ? `opc.tcp://${hub.ip}:4840/` : '-'} small />
          <InfoCell label="NativeStreaming" value={hub?.ip ? `daq.nd://${hub.ip}:7420/` : '-'} small />
        </div>
      </div>

      {/* Message banner */}
      {melding && (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 text-sm rounded-lg p-3 mb-4">
          {melding}
        </div>
      )}

      {/* Channel table */}
      {hubAktiv && <HubKanalTabell />}

      {/* Buffer sync status */}
      {hubAktiv && <BufferStatusCard />}

      {/* Log */}
      <HubLogViewer />
    </>
  )
}

function InfoCell({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <div className="bg-gray-50 rounded-md px-2.5 py-1.5">
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className={`mt-0.5 font-semibold text-gray-900 ${small ? 'text-xs' : 'text-sm'}`}>
        {value || '-'}
      </div>
    </div>
  )
}

function HubKanalTabell() {
  const { t } = useI18n()
  const kanalFetcher = useCallback(() => fetchHubKanalar(), [])
  const { data } = usePolling(kanalFetcher, 3000)
  const kanalar: HubKanal[] = data?.kanalar ?? []
  const feil = (data as Record<string, unknown>)?.feil as string | undefined

  const [synlege, setSynlege] = useState<Set<string>>(() => hentSynlegeKanalar())

  // Override state: key -> { low: string, high: string }
  const [overrides, setOverrides] = useState<Record<string, { low: string; high: string }>>({})
  const [lagreMelding, setLagreMelding] = useState<string | null>(null)
  const [lagrar, setLagrar] = useState(false)

  // Last eksisterande overrides ved oppstart
  useEffect(() => {
    fetchKanalRanges().then(res => {
      const map: Record<string, { low: string; high: string }> = {}
      for (const o of res.overstyringer) {
        const key = `${o.node_id}:${o.kanal_namn}`
        map[key] = { low: String(o.range_low), high: String(o.range_high) }
      }
      setOverrides(map)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    lagreSynlegeKanalar(synlege)
  }, [synlege])

  const toggleSynleg = (key: string) => {
    setSynlege(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const kanalKey = (k: HubKanal) => `${k.node_id}:${k.namn}`

  const alleValgt = kanalar.length > 0 && kanalar.every(k => synlege.has(kanalKey(k)))
  const toggleAlle = () => {
    setSynlege(prev => {
      const next = new Set(prev)
      if (alleValgt) {
        kanalar.forEach(k => next.delete(kanalKey(k)))
      } else {
        kanalar.forEach(k => next.add(kanalKey(k)))
      }
      return next
    })
  }

  const handleOverrideChange = (key: string, field: 'low' | 'high', value: string) => {
    setOverrides(prev => ({
      ...prev,
      [key]: { ...prev[key] || { low: '', high: '' }, [field]: value },
    }))
  }

  const handleLagre = async () => {
    setLagrar(true)
    setLagreMelding(null)
    const overstyringer: HubKanalRangeOverstyring[] = []
    for (const k of kanalar) {
      const key = kanalKey(k)
      const ov = overrides[key]
      if (ov && ov.low !== '' && ov.high !== '') {
        const low = parseFloat(ov.low)
        const high = parseFloat(ov.high)
        if (!isNaN(low) && !isNaN(high) && low < high) {
          overstyringer.push({
            node_id: k.node_id,
            kanal_namn: k.namn,
            range_low: low,
            range_high: high,
            aktiv: true,
          })
        }
      }
    }
    try {
      const res = await oppdaterKanalRanges(overstyringer)
      setLagreMelding(res.melding)
    } catch (e) {
      setLagreMelding(`${t('Error')}: ${e}`)
    }
    setLagrar(false)
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
      <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-2">{t('Hub channels (table)')}</h3>
      {feil && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-lg p-2 mb-2">{feil}</div>
      )}
      {kanalar.length === 0 ? (
        <p className="text-sm text-gray-500 py-4 text-center">{t('No channels available')}</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  <th className="text-center px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 40 }}>
                    <input type="checkbox" checked={alleValgt} onChange={toggleAlle} title={t('Select all / deselect all')} className="accent-[#D76428]" />
                  </th>
                  <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold">{t('Node')}</th>
                  <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold">{t('Channel')}</th>
                  <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 70 }}>{t('Unit')}</th>
                  <th className="text-right px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 100 }}>{t('Value')}</th>
                  <th className="text-right px-1 py-2 border-b-2 border-gray-200 text-gray-400 text-xs uppercase tracking-wider font-semibold" style={{ width: 120 }}>{t('Detected range')}</th>
                  <th className="text-center px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 100 }}>{t('Override min')}</th>
                  <th className="text-center px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 100 }}>{t('Override max')}</th>
                </tr>
              </thead>
              <tbody>
                {kanalar.map((k, i) => {
                  const key = kanalKey(k)
                  const ov = overrides[key] || { low: '', high: '' }
                  return (
                    <tr key={`${k.node_id}-${k.namn}-${i}`} className={`hover:bg-gray-50 transition-colors ${k.overstyrt ? 'bg-orange-50/40' : ''}`}>
                      <td className="text-center px-1 py-1.5 border-b border-gray-100">
                        <input type="checkbox" checked={synlege.has(key)} onChange={() => toggleSynleg(key)} className="accent-[#D76428]" title={t('Show in dashboard and sidebar')} />
                      </td>
                      <td className="px-1 py-1.5 border-b border-gray-100 text-gray-700">{k.node_namn}</td>
                      <td className="px-1 py-1.5 border-b border-gray-100 text-gray-900">{k.namn}</td>
                      <td className="px-1 py-1.5 border-b border-gray-100 text-gray-500">{k.eining || '-'}</td>
                      <td className="px-1 py-1.5 border-b border-gray-100 text-right font-mono text-sm text-green-700">
                        {k.verdi !== null ? k.verdi.toFixed(2) : '-'}
                      </td>
                      <td className="px-1 py-1.5 border-b border-gray-100 text-right text-xs text-gray-400 font-mono">
                        {k.auto_range_low !== undefined ? `[${k.auto_range_low}, ${k.auto_range_high}]` : '-'}
                      </td>
                      <td className="px-1 py-1.5 border-b border-gray-100 text-center">
                        <input
                          type="number"
                          value={ov.low}
                          onChange={e => handleOverrideChange(key, 'low', e.target.value)}
                          placeholder={t('auto')}
                          className="w-20 text-xs text-center border border-gray-200 rounded px-1 py-0.5 focus:border-[#D76428] focus:outline-none"
                        />
                      </td>
                      <td className="px-1 py-1.5 border-b border-gray-100 text-center">
                        <input
                          type="number"
                          value={ov.high}
                          onChange={e => handleOverrideChange(key, 'high', e.target.value)}
                          placeholder={t('auto')}
                          className="w-20 text-xs text-center border border-gray-200 rounded px-1 py-0.5 focus:border-[#D76428] focus:outline-none"
                        />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={handleLagre}
              disabled={lagrar}
              className="text-xs font-medium px-4 py-1.5 rounded-md bg-[#D76428] text-white hover:bg-[#c55a23] disabled:opacity-50 transition-colors"
            >
              {lagrar ? t('Saving...') : t('Save ranges')}
            </button>
            <span className="text-xs text-gray-500">{t('Reconnect required after saving')}</span>
          </div>
          {lagreMelding && (
            <div className="mt-2 bg-blue-50 border border-blue-200 text-blue-800 text-xs rounded-lg p-2">{lagreMelding}</div>
          )}
        </>
      )}
    </div>
  )
}

function HubLogViewer() {
  const { t } = useI18n()
  const logFetcher = useCallback(() =>
    fetch('/api/hub/logg', { credentials: 'include' }).then(r => r.json()),
    []
  )
  const { data } = usePolling(logFetcher, 3000)
  const linjer: string[] = data?.linjer ?? []

  if (linjer.length === 0) return null

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-2">{t('Hub log')}</h3>
      <div className="bg-gray-900 text-gray-300 text-xs font-mono p-3 rounded-lg max-h-48 overflow-y-auto">
        {linjer.slice(-50).map((l, i) => (
          <div key={i} className="whitespace-pre-wrap">{l}</div>
        ))}
      </div>
    </div>
  )
}
