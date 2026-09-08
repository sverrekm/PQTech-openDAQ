import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchSkann, startSkann, stoppSkann } from '../api/nettskann'
import type { SkannStatus } from '../api/nettskann'
import { useI18n } from '../i18n'

/**
 * Nett-skann — kva står på instrumentnettet.
 *
 * Skannar frå containeren, altså gjennom same rute som målepollinga vil
 * bruke. Svarar eit instrument her, når openDAQ-brua det òg.
 */
export default function NettSkannCard() {
  const { t } = useI18n()
  const [subnett, setSubnett] = useState('')
  const [feil, setFeil] = useState<string | null>(null)

  const fetcher = useCallback(() => fetchSkann(), [])
  // Tett polling medan skannet går, roleg elles.
  const { data, refresh } = usePolling<SkannStatus>(fetcher, 2000)
  const koeyrer = data?.tilstand === 'koeyrer'

  const start = async () => {
    const s = subnett.trim()
    if (!s) { setFeil(t('Enter a subnet, e.g. 192.168.50.0/24')); return }
    setFeil(null)
    try {
      const res = await startSkann(s)
      if (!res.suksess) setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    }
  }

  const stopp = async () => {
    try { await stoppSkann(); refresh() } catch { /* status viser resten */ }
  }

  const prosent = data && data.totalt
    ? Math.round((data.ferdig / data.totalt) * 100) : 0

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h2 className="text-base font-semibold text-gray-800 mb-1">
        {t('Network scan')}
      </h2>
      <p className="text-xs text-gray-500 mb-3">
        {t('Scans from the node itself, through the same route the measurement polling uses.')}
      </p>

      {feil && (
        <div className="p-2 mb-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{feil}</div>
      )}

      <div className="flex gap-2 items-end mb-3">
        <div className="flex-1">
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('Subnet')}</label>
          <input
            type="text"
            value={subnett}
            onChange={(e) => setSubnett(e.target.value)}
            placeholder="192.168.50.0/24"
            disabled={koeyrer}
            className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none disabled:bg-gray-50"
          />
        </div>
        {koeyrer ? (
          <button
            onClick={stopp}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            {t('Stop')}
          </button>
        ) : (
          <button
            onClick={start}
            className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520]"
          >
            {t('Scan')}
          </button>
        )}
      </div>

      {koeyrer && (
        <div className="mb-3">
          <div className="h-1.5 bg-gray-100 rounded overflow-hidden">
            <div className="h-full bg-[#D76428] transition-all" style={{ width: `${prosent}%` }} />
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {data?.subnett} — {data?.ferdig}/{data?.totalt} ({prosent}%)
            {data?.melding ? ` · ${data.melding}` : ''}
          </div>
        </div>
      )}

      {!koeyrer && data?.melding && (
        <div className="text-xs text-gray-500 mb-2">
          {data.subnett}: {data.melding}
        </div>
      )}

      {data && data.funn.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
                <th className="py-1.5 pr-3 font-medium">{t('Address')}</th>
                <th className="py-1.5 pr-3 font-medium">{t('Open ports')}</th>
                <th className="py-1.5 font-medium">{t('Identification')}</th>
              </tr>
            </thead>
            <tbody>
              {data.funn.map((f) => (
                <tr key={f.ip} className="border-b border-gray-100 align-top">
                  <td className="py-1.5 pr-3 font-mono text-xs whitespace-nowrap">{f.ip}</td>
                  <td className="py-1.5 pr-3">
                    {f.portar.length === 0 ? (
                      <span className="text-xs text-gray-400">{t('answers ping only')}</span>
                    ) : (
                      <span className="flex flex-wrap gap-1">
                        {f.portar.map((p) => (
                          <span
                            key={p.port}
                            title={`${p.port}`}
                            className={
                              'text-xs px-1.5 py-0.5 rounded ' +
                              (p.port === 502 || p.port === 4840
                                ? 'bg-[#D76428]/10 text-[#D76428] font-medium'
                                : 'bg-gray-100 text-gray-600')
                            }
                          >
                            {p.port} {p.namn}
                          </span>
                        ))}
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 text-xs text-gray-600">
                    {f.server && <div>{f.server}</div>}
                    {f.tittel && <div className="text-gray-500">{f.tittel}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
