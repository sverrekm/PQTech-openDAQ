import type { ReactNode } from 'react'
import type { HubKanal } from '../api/types'
import { useI18n } from '../i18n'
import LiveSparkline from '../components/LiveSparkline'

interface Props {
  nodeId: string
  namn: string
  hubKanalar: HubKanal[]
  onBack?: () => void
}

/** Detaljvising for ein kanal hubben tek imot frå ein remote node
 *  (openDAQ-push eller Modbus TCP). */
export default function HubChannelPage({ nodeId, namn, hubKanalar, onBack }: Props) {
  const { t } = useI18n()
  const kanal = hubKanalar.find(k => k.node_id === nodeId && k.namn === namn)

  if (!kanal) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        {t('Channel not found.')}
      </div>
    )
  }

  const erModbus = kanal.kanal_type === 'modbus'
  const typeLabel = erModbus ? t('Modbus TCP') : 'openDAQ'
  const lav = kanal.overstyrt ? kanal.cr_low : kanal.auto_range_low
  const hog = kanal.overstyrt ? kanal.cr_high : kanal.auto_range_high
  const harRange = lav !== undefined && hog !== undefined

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
            {kanal.namn}
            <span className="text-sm text-gray-500 font-normal ml-2">{kanal.node_namn}</span>
            {kanal.eining && <span className="text-sm text-gray-500 font-normal ml-1">{kanal.eining}</span>}
          </div>
        </div>

        <div className="flex items-baseline gap-6 mb-4">
          <div className="text-4xl font-bold tabular-nums" style={{ color: '#0d9488' }}>
            {kanal.verdi !== null ? kanal.verdi.toFixed(2) : '—'}
            <span className="text-lg font-normal text-gray-500 ml-1">{kanal.eining}</span>
          </div>
        </div>

        <LiveSparkline value={kanal.verdi} color="#0d9488" />
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2>{t('Configuration')}</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
          <Cell label={t('Source')} value={kanal.node_namn} />
          <Cell label={t('Type')} value={typeLabel} />
          <Cell label={t('Unit')} value={kanal.eining || '-'} />
          <Cell label={t('Range')} value={harRange ? `${lav} / ${hog}` : '-'} />
          {erModbus && <Cell label={t('Modbus address')} value={kanal.modbus_adresse ?? '-'} />}
          <Cell label={t('Overridden')} value={kanal.overstyrt ? t('Yes') : t('No')} />
          <Cell label={t('Connected')} value={kanal.tilkobla ? t('Yes') : t('No')} />
        </div>
      </div>
    </>
  )
}

function Cell({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1 font-semibold text-gray-900">{value}</div>
    </div>
  )
}
