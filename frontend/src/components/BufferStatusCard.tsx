import { useCallback } from 'react'
import type { BufferStatus, HubBufferStatus } from '../api/types'
import { fetchBufferStatus, fetchHubBufferStatus } from '../api/buffer'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'

function formatTs(ms: number | null | undefined): string {
  if (!ms) return '-'
  return new Date(ms).toLocaleString()
}

/** Remote node buffer status (shown on SettingsPage or Dashboard) */
export function RemoteBufferStatusCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchBufferStatus(), [])
  const { data: status } = usePolling(fetcher, 5000)

  if (!status || !status.aktivert) return null

  const pct = status.maks_storleik_mb
    ? Math.min(100, ((status.storleik_mb ?? 0) / status.maks_storleik_mb) * 100)
    : 0

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
      <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-2">
        {t('Measurement buffer')}
      </h3>

      {/* Progress bar */}
      <div className="mb-3">
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>{(status.storleik_mb ?? 0).toFixed(1)} MB</span>
          <span>{status.maks_storleik_mb} MB</span>
        </div>
        <div className="w-full bg-gray-200 rounded-full h-2.5">
          <div
            className={`h-2.5 rounded-full transition-all ${
              pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-[#D76428]'
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-2">
        <InfoCell label={t('Total rows')} value={String(status.totalt_rader ?? 0)} />
        <InfoCell label={t('Unsynced')} value={String(status.usynkroniserte ?? 0)} />
        <InfoCell label={t('Synced')} value={String(status.synkroniserte ?? 0)} />
        <InfoCell label={t('Write rate')} value={`${status.skriv_per_sek ?? 0}/s`} />
        <InfoCell label={t('Sample rate')} value={`${((status.sample_rate ?? 20000) / 1000).toFixed(0)} kHz`} />
        <InfoCell label={t('RAM buffer')} value={`${status.ram_buffer_storleik_mb ?? 0} MB`} />
        <InfoCell label={t('Events')} value={String(status.hendingar_totalt ?? 0)} />
        <InfoCell label={t('MQTT log')} value={String(status.mqtt_logg_rader ?? 0)} />
        <InfoCell label={t('Storage')} value={status.ssd_aktiv ? 'SSD' : 'SD'} />
        <InfoCell label={t('Oldest')} value={formatTs(status.eldste_ts)} />
        <InfoCell label={t('Newest')} value={formatTs(status.nyaste_ts)} />
      </div>

      {status.lagringssti && (
        <div className="mt-2 text-xs text-gray-400 truncate" title={status.lagringssti}>
          {status.lagringssti}
        </div>
      )}

      {!status.skalering_klar && (
        <div className="mt-2 text-xs text-yellow-600">
          {t('Calibrating — raw values until ADC nullpoint calibration completes')}
        </div>
      )}
    </div>
  )
}

/** Hub buffer status (shown on HubPage) */
export default function BufferStatusCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchHubBufferStatus(), [])
  const { data: status } = usePolling(fetcher, 5000)

  if (!status || !status.aktivert) return null

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
      <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-2">
        {t('Hub measurement buffer')}
      </h3>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-2 mb-3">
        <InfoCell label={t('Total rows')} value={String(status.totalt_rader ?? 0)} />
        <InfoCell label={t('DB size')} value={`${(status.storleik_mb ?? 0).toFixed(1)} MB`} />
        <InfoCell label={t('Retention')} value={`${status.retensjon_dagar ?? '-'} ${t('days')}`} />
        <InfoCell label={t('Sync interval')} value={`${status.sync_intervall_sek ?? '-'}s`} />
        <InfoCell label={t('Oldest')} value={formatTs(status.eldste_ts)} />
        <InfoCell label={t('Newest')} value={formatTs(status.nyaste_ts)} />
      </div>

      {/* Per-node sync status */}
      {status.nodar && status.nodar.length > 0 && (
        <div>
          <h4 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">
            {t('Node sync status')}
          </h4>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                <th className="text-left px-1 py-1 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Node')}</th>
                <th className="text-right px-1 py-1 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Rows')}</th>
                <th className="text-right px-1 py-1 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Total synced')}</th>
                <th className="text-right px-1 py-1 border-b-2 border-gray-200 text-gray-500 text-xs uppercase font-semibold">{t('Last sync')}</th>
              </tr>
            </thead>
            <tbody>
              {status.nodar.map(n => (
                <tr key={n.node_id} className="hover:bg-gray-50">
                  <td className="px-1 py-1 border-b border-gray-100 text-gray-700">{n.node_id}</td>
                  <td className="px-1 py-1 border-b border-gray-100 text-right font-mono">{n.rader_i_hub}</td>
                  <td className="px-1 py-1 border-b border-gray-100 text-right font-mono">{n.antal_henta}</td>
                  <td className="px-1 py-1 border-b border-gray-100 text-right text-gray-500 text-xs">
                    {n.siste_sync ? new Date(n.siste_sync).toLocaleTimeString() : '-'}
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

function InfoCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-50 rounded-md px-2.5 py-1.5">
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className="mt-0.5 text-sm font-semibold text-gray-900">{value || '-'}</div>
    </div>
  )
}
