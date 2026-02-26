import { useState, useEffect } from 'react'
import type { EnhetKonfig } from '../api/types'
import { fetchEnhetKonfig, oppdaterEnhetKonfig } from '../api/enhet'

export default function DeviceSettingsCard() {
  const [konfig, setKonfig] = useState<EnhetKonfig>({ antal_adc_kanalar: 8, modell: '', location: '' })
  const [melding, setMelding] = useState<string | null>(null)
  const [lagrar, setLagrar] = useState(false)

  useEffect(() => {
    fetchEnhetKonfig().then(setKonfig).catch(() => {})
  }, [])

  const lagre = async () => {
    setLagrar(true)
    setMelding(null)
    try {
      const res = await oppdaterEnhetKonfig(konfig)
      setMelding(res.melding)
    } catch (e) {
      setMelding('Feil ved lagring')
    }
    setLagrar(false)
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3">Enhet</h2>

      <div className="grid grid-cols-2 gap-4 mb-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Antal ADC-kanalar</label>
          <select
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
            value={konfig.antal_adc_kanalar}
            onChange={e => setKonfig({ ...konfig, antal_adc_kanalar: parseInt(e.target.value) })}
          >
            {[4, 8, 16].map(n => (
              <option key={n} value={n}>{n} kanalar</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Modell</label>
          <input
            type="text"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
            value={konfig.modell}
            onChange={e => setKonfig({ ...konfig, modell: e.target.value })}
          />
        </div>
      </div>
      <div className="mb-4">
        <label className="block text-xs font-medium text-gray-600 mb-1">Location (visest i DewesoftX)</label>
        <input
          type="text"
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
          value={konfig.location}
          onChange={e => setKonfig({ ...konfig, location: e.target.value })}
          placeholder="t.d. Verksted, Tavle 3, Sundet"
        />
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={lagre}
          disabled={lagrar}
          className="px-4 py-2 bg-[#D76428] text-white text-sm font-medium rounded-md hover:bg-[#c0571f] disabled:opacity-50 transition-colors"
        >
          {lagrar ? 'Lagrar...' : 'Lagre'}
        </button>
        {melding && (
          <span className="text-sm text-gray-600">{melding}</span>
        )}
      </div>
    </div>
  )
}
