import { useState, useCallback } from 'react'
import { fetchSiriusStatus, siriusGjenopplivEp2 } from '../api/sirius'
import { usePolling } from '../hooks/usePolling'

export default function Ep2RecoveryCard() {
  const fetcher = useCallback(() => fetchSiriusStatus(), [])
  const { data: s, refresh } = usePolling(fetcher, 5000)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  // Only show when connected but EP2 is not OK
  if (!s || !s.tilgjengelig || !s.tilkoblet || s.ep2_ok !== false) return null

  const handle = async () => {
    setBusy(true)
    try {
      const res = await siriusGjenopplivEp2()
      setMelding({ text: res.melding, ok: res.suksess })
      refresh()
    } catch (e) {
      setMelding({ text: 'Nettverksfeil', ok: false })
    }
    setBusy(false)
  }

  return (
    <div className="kort">
      <h2>EP2 Gjenoppliving</h2>
      <p style={{ color: '#991b1b', fontSize: '0.85rem', marginBottom: '0.75rem' }}>
        EP2 (data-endepunkt) er nede. Proev aa gjenopplive med ulike strategiar.
      </p>
      <button className="btn btn-gul" disabled={busy} onClick={handle}>
        {busy ? 'Proever strategiar...' : 'Gjenoppliv EP2'}
      </button>
      {melding && (
        <div className={`melding ${melding.ok ? 'melding-ok' : 'melding-feil'}`}>
          {melding.text}
        </div>
      )}
    </div>
  )
}
