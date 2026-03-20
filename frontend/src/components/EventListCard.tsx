import { useCallback } from 'react'
import type { BufferHending } from '../api/types'
import { fetchBufferHendingar } from '../api/buffer'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'

const TYPE_COLORS: Record<string, string> = {
  rms_terskel: 'bg-red-100 text-red-700',
  dvdt: 'bg-orange-100 text-orange-700',
  mqtt_endring: 'bg-blue-100 text-blue-700',
}

export default function EventListCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchBufferHendingar(30), [])
  const { data } = usePolling(fetcher, 5000)
  const hendingar: BufferHending[] = data?.hendingar ?? []

  if (hendingar.length === 0) return null

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
      <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-2">
        {t('Events')}
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className="text-left px-1 py-1.5 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Time')}</th>
              <th className="text-left px-1 py-1.5 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Type')}</th>
              <th className="text-left px-1 py-1.5 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Description')}</th>
              <th className="text-right px-1 py-1.5 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Duration')}</th>
            </tr>
          </thead>
          <tbody>
            {hendingar.map(h => (
              <tr key={h.id} className="hover:bg-gray-50 transition-colors">
                <td className="px-1 py-1.5 border-b border-gray-100 text-gray-700 text-xs whitespace-nowrap">
                  {new Date(h.tidsstempel_ms).toLocaleString()}
                </td>
                <td className="px-1 py-1.5 border-b border-gray-100">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${TYPE_COLORS[h.type] ?? 'bg-gray-100 text-gray-600'}`}>
                    {t(h.type) || h.type}
                  </span>
                </td>
                <td className="px-1 py-1.5 border-b border-gray-100 text-gray-600 text-xs max-w-[300px] truncate" title={h.skildring}>
                  {h.skildring}
                </td>
                <td className="px-1 py-1.5 border-b border-gray-100 text-right text-gray-500 text-xs font-mono">
                  {h.varigheit_ms ? `${h.varigheit_ms} ms` : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
