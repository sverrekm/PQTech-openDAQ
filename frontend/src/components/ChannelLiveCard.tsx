import { useRef } from 'react'
import type { KanalKonfig, KanalLive, MqttStatus } from '../api/types'
import SparklineChart from './SparklineChart'

interface Props {
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
  onChannelClick: (index: number) => void
  loading?: boolean
}

export default function ChannelLiveCard({ kanalar, liveData: live, mqttStatus, onChannelClick, loading = false }: Props) {
  const sparkDataRef = useRef<Map<number, number[]>>(new Map())

  if (loading || !kanalar) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm flex justify-center items-center h-48">
        <svg className="animate-spin h-8 w-8 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

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
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">Kanalar - Live</h2>
      <table className="w-full border-collapse text-sm mt-3">
        <thead>
          <tr>
            <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 30 }}>#</th>
            <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold">Namn</th>
            <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 55 }}>Eining</th>
            <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 45 }}>Aktiv</th>
            <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 100 }}>Verdi</th>
            <th className="text-left px-1 py-2 border-b-2 border-gray-200 text-gray-500 text-xs uppercase tracking-wider font-semibold" style={{ width: 80 }}>Trend</th>
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
                className={`transition-colors duration-100 ease-in-out ${k.aktiv ? 'cursor-pointer hover:bg-gray-50' : 'opacity-50'}`}
                onClick={k.aktiv ? () => onChannelClick(i) : undefined}
              >
                <td className="px-1 py-1 border-b border-gray-100 align-middle text-gray-500 font-semibold">{i + 1}</td>
                <td className="px-1 py-1 border-b border-gray-100 align-middle">{k.namn}</td>
                <td className="px-1 py-1 border-b border-gray-100 align-middle">{k.enhet}</td>
                <td className="px-1 py-1 border-b border-gray-100 align-middle">
                  <span className={`inline-block px-2 py-1 rounded-md text-xs font-medium ${k.aktiv ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                    {k.aktiv ? 'Ja' : 'Nei'}
                  </span>
                </td>
                <td className="font-mono text-sm text-right px-1 py-1 border-b border-gray-100 align-middle" style={{ color: cv?.color || '#6b6b6b' }}>
                  {cv ? (
                    <>
                      {typeof cv.value === 'number' ? cv.value.toFixed(2) : cv.value}
                      <span className="text-xs opacity-70 ml-1">{cv.source}</span>
                    </>
                  ) : '-'}
                </td>
                <td className="px-1 py-1 border-b border-gray-100 align-middle">
                  <SparklineChart data={[...sparkArr]} />
                </td>
              </tr>
            )
          })}
          {mqttStatus?.aktivert && mqttStatus.topics && Object.entries(mqttStatus.topics).map(([topic, info]) => {
            const mqttKey = `mqtt_${topic}`
            const mqttIdx = 1000 + Object.keys(mqttStatus.topics).indexOf(topic)
            if (info.verdi !== null) {
              const arr = sparkDataRef.current.get(mqttIdx) || []
              arr.push(info.verdi)
              if (arr.length > 30) arr.shift()
              sparkDataRef.current.set(mqttIdx, arr)
            }
            const sparkArr = sparkDataRef.current.get(mqttIdx) || []

            return (
              <tr key={mqttKey} className="transition-colors duration-100 ease-in-out bg-purple-50/30">
                <td className="px-1 py-1 border-b border-gray-100 align-middle text-purple-500 font-semibold text-xs">M</td>
                <td className="px-1 py-1 border-b border-gray-100 align-middle">
                  {info.namn || topic}
                  <span className="text-xs text-purple-400 ml-1">MQTT</span>
                </td>
                <td className="px-1 py-1 border-b border-gray-100 align-middle">{info.enhet || '-'}</td>
                <td className="px-1 py-1 border-b border-gray-100 align-middle">
                  <span className={`inline-block px-2 py-1 rounded-md text-xs font-medium ${info.verdi !== null ? 'bg-purple-100 text-purple-800' : 'bg-yellow-100 text-yellow-800'}`}>
                    {info.verdi !== null ? 'Ja' : 'Ventar'}
                  </span>
                </td>
                <td className="font-mono text-sm text-right px-1 py-1 border-b border-gray-100 align-middle" style={{ color: '#8b5cf6' }}>
                  {info.verdi !== null ? (
                    <>
                      {info.verdi.toFixed(2)}
                      <span className="text-xs opacity-70 ml-1">MQTT</span>
                    </>
                  ) : '-'}
                </td>
                <td className="px-1 py-1 border-b border-gray-100 align-middle">
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
