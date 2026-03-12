import type { ServerStatus } from '../api/types'
import InfoGrid from './InfoGrid'
import { useI18n } from '../i18n'

interface Props {
  status: ServerStatus | null
}

export default function ServerStatusCard({ status: s }: Props) {
  const { t } = useI18n()
  if (!s) return null

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">{t('Server')}</h2>
      <InfoGrid items={[
        { label: t('IP address'), value: s.ip || '-' },
        { label: t('Device'), value: s.enhet_navn || t('Searching...') },
        { label: t('Protocols'), value: s.servere.length > 0 ? s.servere.join(', ') : '-' },
        { label: t('Channels'), value: s.kanaler.length > 0 ? s.kanaler.length : '-' },
      ]} />
      {s.kanaler.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-2">
          {s.kanaler.map((k, i) => (
            <span key={i} className="inline-block px-2 py-1 rounded-md text-xs font-medium bg-green-100 text-green-800">{k}</span>
          ))}
        </div>
      )}
    </div>
  )
}
