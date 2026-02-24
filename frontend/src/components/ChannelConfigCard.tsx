import { Fragment, useState, useEffect, useCallback } from 'react'
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
    return (k.sensor_ut_2 - k.sensor_inn_1) / (k.sensor_inn_2 - k.sensor_inn_1)
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">Kanal-konfigurasjon</h2>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-8">#</th>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-36">Namn</th>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-24">Type</th>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-16">Min</th>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-16">Maks</th>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-20">Eining</th>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-20">Sensor</th>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-20">Exc.</th>
              <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-16">Aktiv</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {kanalar.map((k, i) => (
              <Fragment key={i}>
                <tr className={k.aktiv ? '' : 'opacity-60'}>
                  <td className="px-2 py-2 whitespace-nowrap text-sm font-medium text-gray-500">{i + 1}</td>
                  <td className="px-2 py-2 whitespace-nowrap text-sm text-gray-900">
                    <input
                      type="text"
                      value={k.namn}
                      onChange={e => oppdater(i, 'namn', e.target.value)}
                      className="block w-full text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                    />
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap text-sm text-gray-900">
                    <select
                      value={k.type}
                      onChange={e => oppdater(i, 'type', e.target.value)}
                      className="block w-full text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                    >
                      {GYLDIGE_TYPAR.map(t => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap text-sm text-gray-900">
                    <input
                      type="number"
                      value={k.range_min}
                      onChange={e => oppdater(i, 'range_min', parseFloat(e.target.value) || 0)}
                      className="block w-full text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                      step="any"
                    />
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap text-sm text-gray-900">
                    <input
                      type="number"
                      value={k.range_max}
                      onChange={e => oppdater(i, 'range_max', parseFloat(e.target.value) || 0)}
                      className="block w-full text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                      step="any"
                    />
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap text-sm text-gray-900">
                    <select
                      value={k.enhet}
                      onChange={e => oppdater(i, 'enhet', e.target.value)}
                      className="block w-full text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                    >
                      {GYLDIGE_EINHEITAR.map(e => (
                        <option key={e} value={e}>{e || '(ingen)'}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap text-sm text-gray-900">
                    <button
                      className={`px-2 py-1 rounded-md text-xs font-medium transition-colors duration-150 ease-in-out ${k.sensor_aktiv ? 'bg-orange-100 text-orange-800 hover:bg-orange-200' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
                      onClick={() => toggleSensorPanel(i)}
                      title={k.sensor_aktiv ? `${k.sensor_namn || 'Sensor'} (${sensorSlope(k)} ${k.sensor_enhet}/V)` : 'Ingen sensor'}
                    >
                      {k.sensor_aktiv ? k.sensor_enhet || 'On' : 'Av'}
                    </button>
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap text-sm text-gray-900">
                    <select
                      value={k.excitation_v}
                      onChange={e => oppdater(i, 'excitation_v', parseFloat(e.target.value))}
                      title="Excitation-spenning til integrator"
                      className="block w-full text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                    >
                      <option value={0}>Av</option>
                      <option value={5}>5 V</option>
                    </select>
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap text-sm text-gray-900">
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={k.aktiv}
                        onChange={e => oppdater(i, 'aktiv', e.target.checked)}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-[#D76428] rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#D76428]" />
                    </label>
                  </td>
                </tr>
                {opnaSensor.has(i) && (
                  <tr className="bg-gray-50">
                    <td colSpan={9} className="p-0 border-b border-gray-200">
                      <div className="p-4 border-t border-dashed border-gray-300">
                        <div className="flex items-center gap-2 mb-3">
                          <label className="relative inline-flex items-center cursor-pointer">
                            <input
                              type="checkbox"
                              checked={k.sensor_aktiv}
                              onChange={e => oppdater(i, 'sensor_aktiv', e.target.checked)}
                              className="sr-only peer"
                            />
                            <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-[#D76428] rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#D76428]" />
                          </label>
                          <span className="text-sm font-semibold text-gray-600 uppercase tracking-wider">Sensor-skalering</span>
                          {k.sensor_aktiv && (
                            <span className="text-xs font-mono text-[#D76428] ml-auto">
                              slope = {sensorSlope(k).toFixed(2)} {k.sensor_enhet}/V
                            </span>
                          )}
                        </div>
                        {k.sensor_aktiv && (
                          <div className="flex flex-wrap gap-3 items-start">
                            <div className="flex flex-col gap-3">
                              <label className="block">
                                <span className="text-xs text-gray-600 uppercase tracking-wider font-semibold">Sensor-namn</span>
                                <input
                                  type="text"
                                  value={k.sensor_namn}
                                  onChange={e => oppdater(i, 'sensor_namn', e.target.value)}
                                  placeholder="Rogowski 6kA"
                                  className="block w-32 text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                                />
                              </label>
                              <label className="block">
                                <span className="text-xs text-gray-600 uppercase tracking-wider font-semibold">Eining ut</span>
                                <input
                                  type="text"
                                  value={k.sensor_enhet}
                                  onChange={e => oppdater(i, 'sensor_enhet', e.target.value)}
                                  placeholder="A"
                                  className="block w-20 text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                                />
                              </label>
                            </div>
                            <div className="flex items-end gap-2">
                              <div className="text-sm font-semibold text-gray-800 self-center w-12">Punkt 1</div>
                              <label className="block">
                                <span className="text-xs text-gray-600 uppercase tracking-wider font-semibold">Inn (V)</span>
                                <input
                                  type="number"
                                  value={k.sensor_inn_1}
                                  onChange={e => oppdater(i, 'sensor_inn_1', parseFloat(e.target.value) || 0)}
                                  step="any"
                                  className="block w-24 text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                                />
                              </label>
                              <label className="block">
                                <span className="text-xs text-gray-600 uppercase tracking-wider font-semibold">Ut ({k.sensor_enhet || '?'})</span>
                                <input
                                  type="number"
                                  value={k.sensor_ut_1}
                                  onChange={e => oppdater(i, 'sensor_ut_1', parseFloat(e.target.value) || 0)}
                                  step="any"
                                  className="block w-24 text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                                />
                              </label>
                            </div>
                            <div className="flex items-end gap-2">
                              <div className="text-sm font-semibold text-gray-800 self-center w-12">Punkt 2</div>
                              <label className="block">
                                <span className="text-xs text-gray-600 uppercase tracking-wider font-semibold">Inn (V)</span>
                                <input
                                  type="number"
                                  value={k.sensor_inn_2}
                                  onChange={e => oppdater(i, 'sensor_inn_2', parseFloat(e.target.value) || 0)}
                                  step="any"
                                  className="block w-24 text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                                />
                              </label>
                              <label className="block">
                                <span className="text-xs text-gray-600 uppercase tracking-wider font-semibold">Ut ({k.sensor_enhet || '?'})</span>
                                <input
                                  type="number"
                                  value={k.sensor_ut_2}
                                  onChange={e => oppdater(i, 'sensor_ut_2', parseFloat(e.target.value) || 0)}
                                  step="any"
                                  className="block w-24 text-sm rounded-md border-gray-300 shadow-sm focus:border-[#D76428] focus:ring focus:ring-[#D76428] focus:ring-opacity-50"
                                />
                              </label>
                            </div>
                            {k.sensor_inn_2 === k.sensor_inn_1 && (
                              <div className="w-full text-xs text-red-800 bg-red-100 rounded-md p-1.5 mt-1">
                                Inn-punkt 1 og 2 kan ikkje vere like (gir divisjon med null)
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2 mt-3 items-center">
        <button className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out" onClick={lagre}>Lagre</button>
        <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out" onClick={tilbakestill}>Tilbakestill</button>
        {melding && (
          <span className={`text-sm ml-2 ${melding.ok ? 'text-green-700' : 'text-red-700'}`}>
            {melding.text}
          </span>
        )}
      </div>
    </div>
  )
}
