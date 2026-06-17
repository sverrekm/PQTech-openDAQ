import type { ReactNode } from 'react'
import type { MqttStatus } from '../api/types'
import { useI18n } from '../i18n'
import LiveSparkline from '../components/LiveSparkline'

interface Props {
  topic: string
  mqttStatus: MqttStatus | null
  onBack?: () => void
}

/** Detaljvising for ein MQTT-kanal (topic). */
export default function MqttChannelPage({ topic, mqttStatus, onBack }: Props) {
  const { t, lang } = useI18n()
  const info = mqttStatus?.topics?.[topic]

  if (!info) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        {t('Channel not found.')}
      </div>
    )
  }

  const locale = lang === 'nb' ? 'nb-NO' : 'en-US'
  // sist_oppdatert er epoch-sekund (Python time.time())
  const sist = info.sist_oppdatert
    ? new Date(info.sist_oppdatert * 1000).toLocaleString(locale)
    : '-'

  return (
    <>
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <div className="flex items-center gap-3 mb-4">
          {onBack && (
            <button className="text-sm text-gray-500 hover:text-[#D76428] cursor-pointer bg-transparent border-none" onClick={onBack}>
              &#8592; {t('Back')}
            </button>
          )}
          <div className="text-lg font-semibold text-gray-900">
            {info.namn || topic}
            <span className="text-sm text-gray-500 font-normal ml-2">MQTT</span>
            {info.enhet && <span className="text-sm text-gray-500 font-normal ml-1">{info.enhet}</span>}
          </div>
        </div>

        <div className="flex items-baseline gap-6 mb-4">
          <div className="text-4xl font-bold tabular-nums" style={{ color: '#8b5cf6' }}>
            {info.verdi !== null ? info.verdi.toFixed(2) : '—'}
            <span className="text-lg font-normal text-gray-500 ml-1">{info.enhet}</span>
          </div>
        </div>

        <LiveSparkline value={info.verdi} color="#8b5cf6" />
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2>{t('Configuration')}</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
          <Cell label={t('Source')} value="MQTT" />
          <Cell label={t('Topic')} value={topic} />
          <Cell label={t('Unit')} value={info.enhet || '-'} />
          <Cell label={t('Last updated')} value={sist} />
        </div>
      </div>
    </>
  )
}

function Cell({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1 font-semibold text-gray-900 break-all">{value}</div>
    </div>
  )
}
