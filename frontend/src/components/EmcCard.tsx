import { useState, useEffect } from 'react'
import { fetchEmcKonfig, lagreEmcKonfig, testEmc } from '../api/emc'
import type { EmcKonfig } from '../api/emc'
import { useI18n } from '../i18n'

/** EMC/spektral-analyse: reknar FFT på rå SIRIUS-bølgjeform → harmoniske,
 *  THD og spektrum til InfluxDB (Grafana). Krev at «Share to Grafana» er sett. */
export default function EmcCard() {
  const { t } = useI18n()
  const [k, setK] = useState<EmcKonfig>({
    aktivert: false, intervall_s: 5, nettfrekvens: 50,
    n_harmoniske: 50, syklusar: 10, fft_maks_hz: 2000, fft_bins: 200,
  })
  const [busy, setBusy] = useState(false)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => { fetchEmcKonfig().then(setK).catch(() => {}) }, [])

  const sett = (felt: keyof EmcKonfig, v: number | boolean) => setK(p => ({ ...p, [felt]: v }))

  const lagre = async () => {
    setBusy(true); setMelding(null)
    try { await lagreEmcKonfig(k); setMelding({ text: t('Saved.'), ok: true }) }
    catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy(false)
  }
  const test = async () => {
    setBusy(true); setMelding(null)
    try { const r = await testEmc(); setMelding({ text: r.melding || (r.suksess ? 'OK' : t('Failed')), ok: r.suksess }) }
    catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy(false)
  }

  const felt = "w-full text-sm px-3 py-1.5 rounded-md border border-gray-300 focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">{t('EMC / spectral analysis')}</h2>
      <p className="text-sm text-gray-500 -mt-1 mb-4 leading-snug">{t('Compute harmonics, THD and spectrum from the raw SIRIUS waveform and send to InfluxDB/Grafana. Requires Share to Grafana to be set up.')}</p>

      <label className="flex items-center gap-2 mb-3 text-sm text-gray-700">
        <input type="checkbox" checked={k.aktivert} onChange={e => sett('aktivert', e.target.checked)} className="accent-[#D76428]" />
        {t('Enabled')}
      </label>

      <div className="flex flex-wrap gap-2">
        <div className="w-32">
          <label className="block text-xs text-gray-500 mb-1">{t('Mains freq (Hz)')}</label>
          <select className={felt} value={k.nettfrekvens} onChange={e => sett('nettfrekvens', Number(e.target.value))}>
            <option value={50}>50</option>
            <option value={60}>60</option>
          </select>
        </div>
        <div className="w-32">
          <label className="block text-xs text-gray-500 mb-1">{t('Harmonics')}</label>
          <input className={felt} type="number" min={1} max={100} value={k.n_harmoniske} onChange={e => sett('n_harmoniske', Number(e.target.value))} />
        </div>
        <div className="w-32">
          <label className="block text-xs text-gray-500 mb-1">{t('Interval (s)')}</label>
          <input className={felt} type="number" min={1} value={k.intervall_s} onChange={e => sett('intervall_s', Number(e.target.value))} />
        </div>
        <div className="w-32">
          <label className="block text-xs text-gray-500 mb-1">{t('Spectrum max (Hz)')}</label>
          <input className={felt} type="number" min={100} value={k.fft_maks_hz} onChange={e => sett('fft_maks_hz', Number(e.target.value))} />
        </div>
      </div>

      <div className="flex gap-2 mt-3">
        <button onClick={lagre} disabled={busy} className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50">
          {busy ? t('Saving...') : t('Save')}
        </button>
        <button onClick={test} disabled={busy} className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50">
          {t('Test write')}
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
