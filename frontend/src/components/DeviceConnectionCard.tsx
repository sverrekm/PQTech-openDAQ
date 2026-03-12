import { useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type { Enhet, ActionResult } from '../api/types'
import { useI18n } from '../i18n'

export default function DeviceConnectionCard() {
  const { t } = useI18n()
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
    } catch {
      setMelding({ text: t('Error searching'), ok: false })
    }
    setSoker(false)
  }

  const kobleTil = async (tilkobling?: string) => {
    const conn = tilkobling || input.trim()
    if (!conn) {
      setMelding({ text: t('Enter a connection string'), ok: false })
      return
    }
    setKobler(true)
    setMelding(null)
    try {
      const res = await apiPost<ActionResult>('/api/koble-til', { tilkobling: conn })
      setMelding({ text: res.melding, ok: res.suksess })
    } catch (e) {
      setMelding({ text: t('Network error: ') + String(e), ok: false })
    }
    setKobler(false)
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">{t('Connect to device')}</h2>
      <div className="flex gap-2 mt-3">
        <input
          type="text"
          className="flex-1 bg-white border border-gray-300 rounded-lg p-2 font-mono text-sm text-gray-900 outline-none focus:border-[#D76428] focus:ring-2 focus:ring-[#D76428]"
          placeholder="daq.opcua://192.168.1.X"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && kobleTil()}
        />
        <button className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={kobler} onClick={() => kobleTil()}>
          {kobler ? t('Connecting...') : t('Connect')}
        </button>
        <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={soker} onClick={sokEnheter}>
          {soker ? t('Searching devices...') : t('Search')}
        </button>
      </div>
      {melding && (
        <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${melding.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
          {melding.text}
        </div>
      )}
      {enheter.length > 0 && (
        <ul className="list-none p-0 mt-3">
          {enheter.map((e, i) => (
            <li key={i} className="flex items-center justify-between py-2 px-3 border-b border-gray-100 text-sm">
              <div className="flex flex-col gap-0.5">
                <span className="text-gray-900 font-medium">{e.navn}</span>
                <span className="text-gray-500 font-mono text-xs">{e.tilkobling}</span>
              </div>
              <button
                className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out"
                onClick={() => { setInput(e.tilkobling); kobleTil(e.tilkobling) }}
              >
                {t('Connect')}
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-gray-500 text-xs mt-3">
        {t('Enter a connection string or click Search to find devices on the network.')}
        {' '}{t('Example:')} <code className="text-orange-700">daq.opcua://IP</code>,{' '}
        <code className="text-orange-700">daq.ns://IP</code>,{' '}
        <code className="text-orange-700">daqref://device0</code>
      </p>
    </div>
  )
}
