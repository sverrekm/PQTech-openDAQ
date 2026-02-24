import { useState, useCallback } from 'react'
import { fetchSiriusStatus, siriusStart, siriusStopp, siriusRekoble } from '../api/sirius'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'

export default function SiriusStatusCard() {
  const fetcher = useCallback(() => fetchSiriusStatus(), [])
  const { data: s, refresh, loading } = usePolling(fetcher, 3000)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  if (loading) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm flex justify-center items-center h-48">
        <svg className="animate-spin h-8 w-8 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

  if (!s || !s.tilgjengelig) return null

  const action = async (fn: () => Promise<{ suksess: boolean; melding: string }>) => {
    setBusy(true)
    try {
      const res = await fn()
      setMelding({ text: res.melding, ok: res.suksess })
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
    setBusy(false)
    refresh()
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">SIRIUS Direkte</h2>
      <InfoGrid items={[
        {
          label: 'Tilkobling',
          value: s.tilkoblet ? 'Tilkoblet (USB)' : 'Frakoblet',
          color: s.tilkoblet ? '#10b981' : '#ef4444',
        },
        { label: 'Serienummer', value: s.serienummer || '-' },
        {
          label: 'Datastraum',
          value: s.streamer ? 'Aktiv' : 'Inaktiv',
          color: s.streamer ? '#10b981' : '#6b6b6b',
        },
        {
          label: 'Datarate',
          value: s.data_rate_kbs && s.data_rate_kbs > 0
            ? `${s.data_rate_kbs.toFixed(1)} KB/s`
            : '-',
        },
      ]} />
      {s.slot_info && s.slot_info.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-3">
          {s.slot_info.map(slot => (
            <span
              key={slot.kanal}
              className={`inline-block px-2 py-1 rounded-md text-xs font-medium ${slot.aktiv ? 'bg-green-100 text-green-800' : 'bg-orange-100 text-orange-800'}`}
            >
              Slot {slot.kanal}{slot.aktiv ? '' : ' (inaktiv)'}
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2 mt-3">
        {!s.streamer && (
          <button className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={busy} onClick={() => action(siriusStart)}>
            Start
          </button>
        )}
        {s.streamer && (
          <button className="bg-red-100 hover:bg-red-200 text-red-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={busy} onClick={() => action(siriusStopp)}>
            Stopp
          </button>
        )}
        <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={busy} onClick={() => action(siriusRekoble)}>
          Rekoble
        </button>
      </div>
      {melding && (
        <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${melding.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
          {melding.text}
        </div>
      )}
    </div>
  )
}
