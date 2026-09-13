import { useRef, useState, useEffect } from 'react'
import type { KanalKonfig, KanalLive, MqttStatus, HubKanal } from '../api/types'
import { erKanalSynleg, SYNLEGE_EVENT } from '../pages/HubPage'
import MeterGrid, { type Meter } from './MeterGrid'
import { useI18n } from '../i18n'

interface Props {
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
  siriusTilkoblet?: boolean
  onChannelClick?: (index: number) => void
  onMqttClick?: (topic: string) => void
  hubKanalar?: HubKanal[]
  onHubClick?: (nodeId: string, namn: string) => void
}

// Kjelde -> farge (same paletten som ChannelLiveCard).
const KJELDE_FARGE: Record<string, string> = {
  Sirius: '#10b981',
  USB: '#3b82f6',
  Sim: '#D76428',
  MQTT: '#8b5cf6',
}

// Palett for framsende (hub/modbus-sub-node) kanalar, farga per node.
const NODE_PALETT = ['#0d9488', '#d97706', '#db2777', '#2563eb', '#7c3aed', '#059669']

/**
 * Node-dashbordets hero: eit målar-rutenett over aktive kanalar (SIRIUS/USB/
 * Sim) og MQTT-topics, kvar som ei stor mono-avlesing med kjelde-tagg og
 * sparklinje. Brukar same verdi-logikk som ChannelLiveCard.
 */
export default function InstrumentMeterGrid({
  kanalar, liveData: live, mqttStatus, siriusTilkoblet, onChannelClick, onMqttClick,
  hubKanalar, onHubClick,
}: Props) {
  // Re-render straks synleg-utvalet endrar seg (frå filter-kortet).
  const [, setSynlegVer] = useState(0)
  useEffect(() => {
    const h = () => setSynlegVer(v => v + 1)
    window.addEventListener(SYNLEGE_EVENT, h)
    return () => window.removeEventListener(SYNLEGE_EVENT, h)
  }, [])
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
    Object.entries(mqttStatus.topics).forEach(([topic, info]) => {
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
    })
  }

  // Framsende kanalar frå modbus/hub-sub-nodar (t.d. PQube på ein node), farga
  // per node. Kan vere den einaste live-kjelda på ein rein aggregerings-node.
  const synlegeHub = (hubKanalar ?? []).filter(k => erKanalSynleg(`${k.node_id}:${k.namn}`))
  const nodeFarge = new Map<string, string>()
  synlegeHub.forEach(k => {
    if (!nodeFarge.has(k.node_id)) nodeFarge.set(k.node_id, NODE_PALETT[nodeFarge.size % NODE_PALETT.length])
  })
  synlegeHub.forEach(k => {
    const sparkKey = `hub_${k.node_id}:${k.namn}`
    meters.push({
      key: sparkKey,
      num: String(meters.length + 1).padStart(2, '0'),
      namn: k.namn,
      verdi: k.verdi !== null && k.verdi !== undefined ? k.verdi.toFixed(2) : '—',
      eining: k.eining || '',
      kjelde: k.node_namn || k.node_id,
      farge: nodeFarge.get(k.node_id) || '#0d9488',
      spark: k.verdi !== null && k.verdi !== undefined ? pushSpark(sparkKey, k.verdi) : (sparkRef.current.get(sparkKey) || []),
      onClick: onHubClick ? () => onHubClick(k.node_id, k.namn) : undefined,
    })
  })

  return (
    <MeterGrid
      kicker={t('Live acquisition')}
      tittel={t('Instrument')}
      meta={<>
        <span>{siriusTilkoblet ? 'SIRIUS' : synlegeHub.length > 0 ? t('Forwarded') : t('No USB instrument')}</span>
        <span>{meters.length} {t('channels')}</span>
      </>}
      meters={meters}
      tomtekst={t('No live channels yet.')}
    />
  )
}
