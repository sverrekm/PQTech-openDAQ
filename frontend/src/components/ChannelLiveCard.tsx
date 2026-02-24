import { useRef } from 'react'
import type { KanalKonfig, KanalLive } from '../api/types'
import SparklineChart from './SparklineChart'

interface Props {
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  onChannelClick: (index: number) => void
}

export default function ChannelLiveCard({ kanalar, liveData: live, onChannelClick }: Props) {
  const sparkDataRef = useRef<Map<number, number[]>>(new Map())

  if (!kanalar) return null

  const getChannelValue = (idx: number) => {
    const key = `kanal_${idx}`
    const odaq = live?.opendaq?.[key] as { siste?: number; kjelde?: string } | undefined
    const drv = live?.driver?.[key] as { siste?: number | null; antall?: number } | undefined

    if (odaq && odaq.kjelde === 'sirius' && odaq.siste !== undefined) {
      return { value: odaq.siste, source: 'Sirius', color: '#10b981' }
    }
    if (drv && drv.siste !== null && drv.siste !== undefined) {
      return { value: drv.siste, source: 'USB', color: '#3b82f6' }
    }
    if (odaq && odaq.siste !== undefined) {
      return { value: odaq.siste, source: 'Sim', color: '#D76428' }
    }
    return null
  }

  return (
    <div className="kort">
      <h2>Kanalar - Live</h2>
      <table className="kanal-tabell">
        <thead>
          <tr>
            <th style={{ width: 30 }}>#</th>
            <th>Namn</th>
            <th style={{ width: 55 }}>Eining</th>
            <th style={{ width: 45 }}>Aktiv</th>
            <th style={{ width: 100 }}>Verdi</th>
            <th style={{ width: 80 }}>Trend</th>
          </tr>
        </thead>
        <tbody>
          {kanalar.map((k, i) => {
            const cv = getChannelValue(i)
            if (cv !== null) {
              const arr = sparkDataRef.current.get(i) || []
              const numVal = typeof cv.value === 'number' ? cv.value : parseFloat(String(cv.value))
              if (!isNaN(numVal)) {
                arr.push(numVal)
                if (arr.length > 30) arr.shift()
                sparkDataRef.current.set(i, arr)
              }
            }
            const sparkArr = sparkDataRef.current.get(i) || []

            return (
              <tr
                key={i}
                className={`${k.aktiv ? '' : 'inaktiv'} ${k.aktiv ? 'kanal-rad-klikkbar' : ''}`}
                onClick={k.aktiv ? () => onChannelClick(i) : undefined}
              >
                <td style={{ color: '#6b6b6b', fontWeight: 600 }}>{i + 1}</td>
                <td>{k.namn}</td>
                <td>{k.enhet}</td>
                <td>
                  <span className={`tag ${k.aktiv ? 'tag-aktiv' : 'tag-usb'}`}>
                    {k.aktiv ? 'Ja' : 'Nei'}
                  </span>
                </td>
                <td className="kanal-live" style={{ color: cv?.color || '#6b6b6b' }}>
                  {cv ? (
                    <>
                      {typeof cv.value === 'number' ? cv.value.toFixed(2) : cv.value}
                      <span style={{ fontSize: '0.65rem', opacity: 0.7, marginLeft: 3 }}>{cv.source}</span>
                    </>
                  ) : '-'}
                </td>
                <td>
                  <SparklineChart data={[...sparkArr]} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
