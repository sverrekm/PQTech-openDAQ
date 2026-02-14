import { useState, useEffect, useCallback } from 'react'
import { fetchKanalar, oppdaterKanalar, resetKanalar } from '../api/kanalar'
import type { KanalKonfig } from '../api/types'

const GYLDIGE_TYPAR = ['voltage', 'current', 'acceleration', 'temperature', 'generic']
const GYLDIGE_EINHEITAR = ['V', 'A', 'm/s\u00b2', '\u00b0C', 'mV', 'mA', '']

export default function ChannelConfigCard() {
  const [kanalar, setKanalar] = useState<KanalKonfig[]>([])
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  const hent = useCallback(async () => {
    try {
      const data = await fetchKanalar()
      setKanalar(data)
    } catch (e) {
      console.error('Kanal-konfig feil:', e)
    }
  }, [])

  useEffect(() => { hent() }, [hent])

  const oppdater = (idx: number, felt: keyof KanalKonfig, value: string | number | boolean) => {
    setKanalar(prev => prev.map((k, i) =>
      i === idx ? { ...k, [felt]: value } : k
    ))
  }

  const lagre = async () => {
    setMelding({ text: 'Lagrar...', ok: true })
    try {
      const res = await oppdaterKanalar(kanalar)
      setMelding({ text: res.melding, ok: res.suksess })
      if (res.suksess) hent()
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
  }

  const tilbakestill = async () => {
    setMelding({ text: 'Tilbakestiller...', ok: true })
    try {
      const res = await resetKanalar()
      setMelding({ text: res.melding, ok: res.suksess })
      if (res.suksess) hent()
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
  }

  return (
    <div className="kort">
      <h2>Kanal-konfigurasjon</h2>
      <table className="kanal-tabell">
        <thead>
          <tr>
            <th style={{ width: 30 }}>#</th>
            <th style={{ width: 140 }}>Namn</th>
            <th style={{ width: 90 }}>Type</th>
            <th style={{ width: 70 }}>Min</th>
            <th style={{ width: 70 }}>Maks</th>
            <th style={{ width: 55 }}>Eining</th>
            <th style={{ width: 45 }}>Aktiv</th>
          </tr>
        </thead>
        <tbody>
          {kanalar.map((k, i) => (
            <tr key={i} className={k.aktiv ? '' : 'inaktiv'}>
              <td style={{ color: '#6b6b6b', fontWeight: 600 }}>{i + 1}</td>
              <td>
                <input
                  type="text"
                  value={k.namn}
                  onChange={e => oppdater(i, 'namn', e.target.value)}
                  style={{ width: 120 }}
                />
              </td>
              <td>
                <select
                  value={k.type}
                  onChange={e => oppdater(i, 'type', e.target.value)}
                >
                  {GYLDIGE_TYPAR.map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </td>
              <td>
                <input
                  type="number"
                  value={k.range_min}
                  onChange={e => oppdater(i, 'range_min', parseFloat(e.target.value) || 0)}
                  style={{ width: 60 }}
                  step="any"
                />
              </td>
              <td>
                <input
                  type="number"
                  value={k.range_max}
                  onChange={e => oppdater(i, 'range_max', parseFloat(e.target.value) || 0)}
                  style={{ width: 60 }}
                  step="any"
                />
              </td>
              <td>
                <select
                  value={k.enhet}
                  onChange={e => oppdater(i, 'enhet', e.target.value)}
                >
                  {GYLDIGE_EINHEITAR.map(e => (
                    <option key={e} value={e}>{e || '(ingen)'}</option>
                  ))}
                </select>
              </td>
              <td>
                <label className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={k.aktiv}
                    onChange={e => oppdater(i, 'aktiv', e.target.checked)}
                  />
                  <span className="toggle-slider" />
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem', alignItems: 'center' }}>
        <button className="btn btn-gronn" onClick={lagre}>Lagre</button>
        <button className="btn btn-blaa" onClick={tilbakestill}>Tilbakestill</button>
        {melding && (
          <span style={{
            fontSize: '0.8rem',
            marginLeft: '0.5rem',
            color: melding.ok ? '#10b981' : '#ef4444',
          }}>
            {melding.text}
          </span>
        )}
      </div>
    </div>
  )
}
