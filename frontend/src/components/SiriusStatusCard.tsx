import { useState, useCallback } from 'react'
import { fetchSiriusStatus, siriusStart, siriusStopp, siriusRekoble } from '../api/sirius'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'

export default function SiriusStatusCard() {
  const fetcher = useCallback(() => fetchSiriusStatus(), [])
  const { data: s, refresh } = usePolling(fetcher, 3000)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

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
    <div className="kort">
      <h2>SIRIUS Direkte</h2>
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
        <div className="kanal-liste" style={{ marginTop: '0.75rem' }}>
          {s.slot_info.map(slot => (
            <span
              key={slot.kanal}
              className={`tag ${slot.aktiv ? 'tag-aktiv' : 'tag-usb'}`}
            >
              Slot {slot.kanal}{slot.aktiv ? '' : ' (inaktiv)'}
            </span>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
        {!s.streamer && (
          <button className="btn btn-gronn" disabled={busy} onClick={() => action(siriusStart)}>
            Start
          </button>
        )}
        {s.streamer && (
          <button className="btn btn-rod" disabled={busy} onClick={() => action(siriusStopp)}>
            Stopp
          </button>
        )}
        <button className="btn btn-blaa" disabled={busy} onClick={() => action(siriusRekoble)}>
          Rekoble
        </button>
      </div>
      {melding && (
        <div className={`melding ${melding.ok ? 'melding-ok' : 'melding-feil'}`}>
          {melding.text}
        </div>
      )}
    </div>
  )
}
