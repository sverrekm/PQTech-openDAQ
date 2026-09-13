import { useRef } from 'react'
import type { KanalKonfig, KanalLive, MqttStatus } from '../api/types'
import SparklineChart from './SparklineChart'
import { useI18n } from '../i18n'

interface Props {
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
  siriusTilkoblet?: boolean
  onChannelClick?: (index: number) => void
  onMqttClick?: (topic: string) => void
}

interface Meter {
  key: string
  num: string
  namn: string
  verdi: string
  eining: string
  kjelde: string
  farge: string
  spark: number[]
  onClick?: () => void
}

// Kjelde -> farge (same paletten som ChannelLiveCard).
const KJELDE_FARGE: Record<string, string> = {
  Sirius: '#10b981',
  USB: '#3b82f6',
  Sim: '#D76428',
  MQTT: '#8b5cf6',
}

/**
 * Instrument-målarrutenett: hero-seksjonen på dashbordet i blueprint-språket.
 * Kvar aktiv kanal (SIRIUS/USB/Sim) og MQTT-topic vert ein stor mono-avlesing
 * med kjelde-tagg og sparklinje. Auto-fit-grid utnyttar heile breidda.
 */
export default function InstrumentMeterGrid({
  kanalar, liveData: live, mqttStatus, siriusTilkoblet, onChannelClick, onMqttClick,
}: Props) {
  const { t } = useI18n()
  const sparkRef = useRef<Map<string, number[]>>(new Map())

  const getChannelValue = (idx: number) => {
    const key = `kanal_${idx}`
    const odaq = live?.opendaq?.[key] as { siste?: number; kjelde?: string } | undefined
    const drv = live?.driver?.[key] as { siste?: number | null } | undefined
    if (odaq && odaq.kjelde === 'sirius' && odaq.siste !== undefined) return { value: odaq.siste, source: 'Sirius' }
    if (drv && drv.siste !== null && drv.siste !== undefined) return { value: drv.siste, source: 'USB' }
    if (odaq && odaq.siste !== undefined) return { value: odaq.siste, source: 'Sim' }
    return null
  }

  const pushSpark = (key: string, v: number) => {
    const arr = sparkRef.current.get(key) || []
    if (!isNaN(v)) {
      arr.push(v)
      if (arr.length > 30) arr.shift()
      sparkRef.current.set(key, arr)
    }
    return sparkRef.current.get(key) || []
  }

  const meters: Meter[] = []

  if (siriusTilkoblet && kanalar) {
    kanalar.forEach((k, i) => {
      if (!k.aktiv) return
      const cv = getChannelValue(i)
      if (!cv) return
      const num = typeof cv.value === 'number' ? cv.value : parseFloat(String(cv.value))
      meters.push({
        key: `ch_${i}`,
        num: String(i + 1).padStart(2, '0'),
        namn: k.namn,
        verdi: isNaN(num) ? '—' : num.toFixed(2),
        eining: k.enhet || '',
        kjelde: cv.source,
        farge: KJELDE_FARGE[cv.source] || 'var(--color-text)',
        spark: pushSpark(`ch_${i}`, num),
        onClick: onChannelClick ? () => onChannelClick(i) : undefined,
      })
    })
  }

  if (mqttStatus?.aktivert && mqttStatus.topics) {
    Object.entries(mqttStatus.topics).forEach(([topic, info], j) => {
      const num = info.verdi
      meters.push({
        key: `mqtt_${topic}`,
        num: String(meters.length + 1).padStart(2, '0'),
        namn: info.namn || topic,
        verdi: num !== null && num !== undefined ? num.toFixed(2) : '—',
        eining: info.enhet || '',
        kjelde: 'MQTT',
        farge: KJELDE_FARGE.MQTT,
        spark: num !== null && num !== undefined ? pushSpark(`mqtt_${topic}`, num) : (sparkRef.current.get(`mqtt_${topic}`) || []),
        onClick: onMqttClick ? () => onMqttClick(topic) : undefined,
      })
      void j
    })
  }

  const kanalTal = meters.length

  return (
    <section>
      <div className="flex items-end justify-between gap-4 mb-3">
        <div>
          <span className="mono block text-[9px] tracking-[0.16em] uppercase" style={{ color: 'var(--color-accent-700)' }}>
            {t('Live acquisition')}
          </span>
          <h2 className="text-[27px] leading-none mt-0.5" style={{ fontFamily: 'var(--font-heading)', fontWeight: 600 }}>
            {t('Instrument')}
          </h2>
        </div>
        <div className="mono flex gap-5 text-[11px] tracking-[0.06em] uppercase pb-1" style={{ color: 'rgba(26,26,26,0.55)' }}>
          <span>{siriusTilkoblet ? 'SIRIUS' : t('No USB instrument')}</span>
          <span>{kanalTal} {t('channels')}</span>
        </div>
      </div>

      {kanalTal === 0 ? (
        <div className="blueprint p-6 text-center text-sm" style={{ color: 'rgba(26,26,26,0.55)' }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {t('No live channels yet.')}
        </div>
      ) : (
        <div className="blueprint" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(230px,1fr))' }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {meters.map(m => (
            <div
              key={m.key}
              onClick={m.onClick}
              className={m.onClick ? 'cursor-pointer transition-colors hover:bg-black/[0.02]' : ''}
              style={{
                padding: '14px 16px 12px',
                borderRight: '1px solid var(--color-divider)',
                borderBottom: '1px solid var(--color-divider)',
                minWidth: 0,
              }}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="mono text-[10px] tracking-[0.12em]" style={{ color: 'rgba(26,26,26,0.45)' }}>{m.num}</span>
                <span className="mono text-[9px] tracking-[0.12em] uppercase" style={{ color: m.farge }}>{m.kjelde}</span>
              </div>
              <div
                className="mt-1.5 overflow-hidden text-ellipsis whitespace-nowrap"
                style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 15, letterSpacing: '0.01em', textTransform: 'uppercase', color: 'rgba(26,26,26,0.72)' }}
              >
                {m.namn}
              </div>
              <div className="flex items-end justify-between gap-2.5 mt-0.5">
                <div className="flex items-baseline gap-1.5 min-w-0 overflow-hidden">
                  <span className="mono" style={{ fontSize: 30, lineHeight: 1.05, fontWeight: 500, color: m.farge }}>{m.verdi}</span>
                  <span className="mono text-[12px]" style={{ color: 'rgba(26,26,26,0.5)' }}>{m.eining}</span>
                </div>
                <div style={{ width: 72, flexShrink: 0 }}>
                  <SparklineChart data={[...m.spark]} />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
