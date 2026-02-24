import { useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type { Enhet, ActionResult } from '../api/types'

export default function DeviceConnectionCard() {
  const [input, setInput] = useState('')
  const [enheter, setEnheter] = useState<Enhet[]>([])
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)
  const [soker, setSoker] = useState(false)
  const [kobler, setKobler] = useState(false)

  const sokEnheter = async () => {
    setSoker(true)
    setMelding(null)
    try {
      const data = await apiGet<{ enheter: Enhet[] }>('/api/enheter')
      setEnheter(data.enheter || [])
    } catch (e) {
      setMelding({ text: 'Feil ved søk', ok: false })
    }
    setSoker(false)
  }

  const kobleTil = async (tilkobling?: string) => {
    const t = tilkobling || input.trim()
    if (!t) {
      setMelding({ text: 'Skriv inn ei tilkoplingsstreng', ok: false })
      return
    }
    setKobler(true)
    setMelding(null)
    try {
      const res = await apiPost<ActionResult>('/api/koble-til', { tilkobling: t })
      setMelding({ text: res.melding, ok: res.suksess })
    } catch (e) {
      setMelding({ text: 'Nettverksfeil: ' + String(e), ok: false })
    }
    setKobler(false)
  }

  return (
    <div className="kort">
      <h2>Koble til enhet</h2>
      <div className="koble-rad">
        <input
          type="text"
          className="koble-input"
          placeholder="daq.opcua://192.168.1.X"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && kobleTil()}
        />
        <button className="btn btn-gronn" disabled={kobler} onClick={() => kobleTil()}>
          {kobler ? 'Kobler...' : 'Koble til'}
        </button>
        <button className="btn btn-blaa" disabled={soker} onClick={sokEnheter}>
          {soker ? 'Søkjer...' : 'Søk'}
        </button>
      </div>
      {melding && (
        <div className={`melding ${melding.ok ? 'melding-ok' : 'melding-feil'}`}>
          {melding.text}
        </div>
      )}
      {enheter.length > 0 && (
        <ul className="enhet-liste">
          {enheter.map((e, i) => (
            <li key={i}>
              <div className="enhet-info">
                <span className="enhet-navn">{e.navn}</span>
                <span className="enhet-conn">{e.tilkobling}</span>
              </div>
              <button
                className="btn btn-gronn"
                onClick={() => { setInput(e.tilkobling); kobleTil(e.tilkobling) }}
              >
                Koble til
              </button>
            </li>
          ))}
        </ul>
      )}
      <p style={{ color: '#6b6b6b', fontSize: '0.8rem', marginTop: '0.75rem' }}>
        Skriv inn tilkoplingsstreng eller klikk Søk for å finne einingar på nettverket.
        Eksempel: <code style={{ color: '#B85420' }}>daq.opcua://IP</code>,{' '}
        <code style={{ color: '#B85420' }}>daq.ns://IP</code>,{' '}
        <code style={{ color: '#B85420' }}>daqref://device0</code>
      </p>
    </div>
  )
}
