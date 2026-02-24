import { useState, useEffect, useCallback } from 'react'
import { fetchKanalar, oppdaterKanalar, resetKanalar } from '../api/kanalar'
import type { KanalKonfig } from '../api/types'

const GYLDIGE_TYPAR = ['voltage', 'current', 'acceleration', 'temperature', 'generic']
const GYLDIGE_EINHEITAR = ['V', 'A', 'm/s\u00b2', '\u00b0C', 'mV', 'mA', '']

export default function ChannelConfigCard() {
  const [kanalar, setKanalar] = useState<KanalKonfig[]>([])
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)
  const [opnaSensor, setOpnaSensor] = useState<Set<number>>(new Set())

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

  const toggleSensorPanel = (idx: number) => {
    setOpnaSensor(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
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

  // Berekn slope for visning
  const sensorSlope = (k: KanalKonfig) => {
    if (k.sensor_inn_2 === k.sensor_inn_1) return 0
    return (k.sensor_ut_2 - k.sensor_ut_1) / (k.sensor_inn_2 - k.sensor_inn_1)
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
            <th style={{ width: 55 }}>Sensor</th>
            <th style={{ width: 45 }}>Aktiv</th>
          </tr>
        </thead>
        <tbody>
          {kanalar.map((k, i) => (
            <>
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
                  <button
                    className={`btn-sensor-toggle ${k.sensor_aktiv ? 'aktiv' : ''}`}
                    onClick={() => toggleSensorPanel(i)}
                    title={k.sensor_aktiv ? `${k.sensor_namn || 'Sensor'} (${sensorSlope(k)} ${k.sensor_enhet}/V)` : 'Ingen sensor'}
                  >
                    {k.sensor_aktiv ? k.sensor_enhet || 'On' : 'Av'}
                  </button>
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
              {opnaSensor.has(i) && (
                <tr key={`sensor-${i}`} className="sensor-rad">
                  <td colSpan={8}>
                    <div className="sensor-panel">
                      <div className="sensor-panel-header">
                        <label className="toggle-switch">
                          <input
                            type="checkbox"
                            checked={k.sensor_aktiv}
                            onChange={e => oppdater(i, 'sensor_aktiv', e.target.checked)}
                          />
                          <span className="toggle-slider" />
                        </label>
                        <span className="sensor-panel-tittel">Sensor-skalering</span>
                        {k.sensor_aktiv && (
                          <span className="sensor-slope-info">
                            slope = {sensorSlope(k).toFixed(2)} {k.sensor_enhet}/V
                          </span>
                        )}
                      </div>
                      {k.sensor_aktiv && (
                        <div className="sensor-felt">
                          <div className="sensor-felt-rad">
                            <label>
                              <span>Sensor-namn</span>
                              <input
                                type="text"
                                value={k.sensor_namn}
                                onChange={e => oppdater(i, 'sensor_namn', e.target.value)}
                                placeholder="Rogowski 6kA"
                              />
                            </label>
                            <label>
                              <span>Eining ut</span>
                              <input
                                type="text"
                                value={k.sensor_enhet}
                                onChange={e => oppdater(i, 'sensor_enhet', e.target.value)}
                                placeholder="A"
                                style={{ width: 60 }}
                              />
                            </label>
                          </div>
                          <div className="sensor-punkt">
                            <div className="sensor-punkt-tittel">Punkt 1</div>
                            <label>
                              <span>Inn (V)</span>
                              <input
                                type="number"
                                value={k.sensor_inn_1}
                                onChange={e => oppdater(i, 'sensor_inn_1', parseFloat(e.target.value) || 0)}
                                step="any"
                              />
                            </label>
                            <label>
                              <span>Ut ({k.sensor_enhet || '?'})</span>
                              <input
                                type="number"
                                value={k.sensor_ut_1}
                                onChange={e => oppdater(i, 'sensor_ut_1', parseFloat(e.target.value) || 0)}
                                step="any"
                              />
                            </label>
                          </div>
                          <div className="sensor-punkt">
                            <div className="sensor-punkt-tittel">Punkt 2</div>
                            <label>
                              <span>Inn (V)</span>
                              <input
                                type="number"
                                value={k.sensor_inn_2}
                                onChange={e => oppdater(i, 'sensor_inn_2', parseFloat(e.target.value) || 0)}
                                step="any"
                              />
                            </label>
                            <label>
                              <span>Ut ({k.sensor_enhet || '?'})</span>
                              <input
                                type="number"
                                value={k.sensor_ut_2}
                                onChange={e => oppdater(i, 'sensor_ut_2', parseFloat(e.target.value) || 0)}
                                step="any"
                              />
                            </label>
                          </div>
                          {k.sensor_inn_2 === k.sensor_inn_1 && (
                            <div className="sensor-feil">
                              Inn-punkt 1 og 2 kan ikkje vere like (gir divisjon med null)
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </>
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
