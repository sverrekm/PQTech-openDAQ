import { useRef, useState, useEffect } from 'react'
import type { HubKanal } from '../api/types'
import { erKanalSynleg, SYNLEGE_EVENT } from '../pages/HubPage'
import MeterGrid, { type Meter } from './MeterGrid'
import { useI18n } from '../i18n'

interface Props {
  hubKanalar?: HubKanal[]
  onHubClick?: (nodeId: string, namn: string) => void
}

// Palett for å skilje nodane visuelt (kjelde-tagg + verdi-farge per node).
const NODE_PALETT = ['#D76428', '#10b981', '#3b82f6', '#8b5cf6', '#0d9488', '#d97706', '#db2777']

/**
 * Hub-dashbordets hero: eit målar-rutenett over dei synlege kanalane som
 * nodane pushar til hubben, kvar farga per node så kjeldene er lette å skilje.
 * Speglar node-dashbordets InstrumentMeterGrid via delt MeterGrid.
 */
export default function HubMeterGrid({ hubKanalar, onHubClick }: Props) {
  // Re-render straks synleg-utvalet endrar seg (frå filter-kortet).
  const [, setSynlegVer] = useState(0)
  useEffect(() => {
    const h = () => setSynlegVer(v => v + 1)
    window.addEventListener(SYNLEGE_EVENT, h)
    return () => window.removeEventListener(SYNLEGE_EVENT, h)
  }, [])
  const { t } = useI18n()
  const sparkRef = useRef<Map<string, number[]>>(new Map())

  const synlege = (hubKanalar ?? []).filter(k => erKanalSynleg(`${k.node_id}:${k.namn}`))

  // Stabil farge per node_id (rekkjefølgd etter fyrste førekomst).
  const nodeFarge = new Map<string, string>()
  synlege.forEach(k => {
    if (!nodeFarge.has(k.node_id)) nodeFarge.set(k.node_id, NODE_PALETT[nodeFarge.size % NODE_PALETT.length])
  })
  const nodeTal = nodeFarge.size

  const meters: Meter[] = synlege.map((k, i) => {
    const sparkKey = `${k.node_id}:${k.namn}`
    if (k.verdi !== null && k.verdi !== undefined) {
      const arr = sparkRef.current.get(sparkKey) || []
      arr.push(k.verdi)
      if (arr.length > 30) arr.shift()
      sparkRef.current.set(sparkKey, arr)
    }
    return {
      key: sparkKey,
      num: String(i + 1).padStart(2, '0'),
      namn: k.namn,
      verdi: k.verdi !== null && k.verdi !== undefined ? k.verdi.toFixed(2) : '—',
      eining: k.eining || '',
      kjelde: k.node_namn || k.node_id,
      farge: nodeFarge.get(k.node_id) || '#D76428',
      spark: sparkRef.current.get(sparkKey) || [],
      onClick: onHubClick ? () => onHubClick(k.node_id, k.namn) : undefined,
    }
  })

  return (
    <MeterGrid
      kicker={t('Hub — live channels')}
      tittel={t('Fleet')}
      meta={<>
        <span>{nodeTal} {nodeTal === 1 ? t('node') : t('nodes')}</span>
        <span>{meters.length} {t('channels')}</span>
      </>}
      meters={meters}
      tomtekst={t('No channels selected. Pick channels in the filter below.')}
    />
  )
}
