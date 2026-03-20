import { useCallback } from 'react'
import type { MqttLoggRad } from '../api/types'
import { fetchBufferMqttLogg } from '../api/buffer'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'

export default function MqttLogCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchBufferMqttLogg(50), [])
  const { data } = usePolling(fetcher, 5000)
  const rader: MqttLoggRad[] = data?.rader ?? []

  if (rader.length === 0) return null

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
      <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-2">
        {t('MQTT log')}
      </h3>
      <div className="overflow-x-auto max-h-64 overflow-y-auto">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 bg-white">
            <tr>
              <th className="text-left px-1 py-1.5 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Time')}</th>
              <th className="text-left px-1 py-1.5 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Topic')}</th>
              <th className="text-right px-1 py-1.5 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Value')}</th>
            </tr>
          </thead>
          <tbody>
            {rader.map(r => (
              <tr key={r.id} className="hover:bg-gray-50 transition-colors">
                <td className="px-1 py-1 border-b border-gray-100 text-gray-700 text-xs whitespace-nowrap">
                  {new Date(r.tidsstempel_ms).toLocaleTimeString()}
                </td>
                <td className="px-1 py-1 border-b border-gray-100 text-gray-600 text-xs font-mono truncate max-w-[200px]" title={r.topic}>
                  {r.topic}
                </td>
                <td className="px-1 py-1 border-b border-gray-100 text-right font-mono text-sm text-green-700">
                  {r.verdi.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
