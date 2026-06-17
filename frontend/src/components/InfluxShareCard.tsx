import { useState, useEffect } from 'react'
import { fetchInfluxKonfig, lagreInfluxKonfig, testInflux } from '../api/influx'
import type { InfluxKonfig } from '../api/influx'
import { useI18n } from '../i18n'

/** Del kanalverdiar til Grafana via InfluxDB v2. Skriv measurement
 *  pqtech_channel{node,channel,unit} med jamne mellomrom. */
export default function InfluxShareCard() {
  const { t } = useI18n()
  const [aktivert, setAktivert] = useState(false)
  const [url, setUrl] = useState('')
  const [org, setOrg] = useState('')
  const [bucket, setBucket] = useState('')
  const [intervall, setIntervall] = useState(10)
  const [token, setToken] = useState('')
  const [tokenSatt, setTokenSatt] = useState(false)
  const [busy, setBusy] = useState(false)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => {
    fetchInfluxKonfig().then((k: InfluxKonfig) => {
      setAktivert(!!k.aktivert)
      setUrl(k.url || '')
      setOrg(k.org || '')
      setBucket(k.bucket || '')
      setIntervall(k.intervall_s || 10)
      setTokenSatt(!!k.token_satt)
    }).catch(() => {})
  }, [])

  const lagre = async () => {
    setBusy(true); setMelding(null)
    try {
      const res = await lagreInfluxKonfig({
        aktivert, url, org, bucket, intervall_s: Number(intervall) || 10,
        ...(token ? { token } : {}),
      })
      setTokenSatt(!!res.token_satt)
      setToken('')
      setMelding({ text: t('Saved.'), ok: true })
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
    setBusy(false)
  }

  const test = async () => {
    setBusy(true); setMelding(null)
    try {
      const res = await testInflux()
      setMelding({ text: res.melding || (res.suksess ? 'OK' : t('Failed')), ok: res.suksess })
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
    setBusy(false)
  }

  const felt = "w-full text-sm px-3 py-1.5 rounded-md border border-gray-300 focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">{t('Share to Grafana (InfluxDB)')}</h2>
      <p className="text-sm text-gray-500 -mt-1 mb-4 leading-snug">{t('Write channel values to InfluxDB so they can be graphed in Grafana.')}</p>

      <label className="flex items-center gap-2 mb-3 text-sm text-gray-700">
        <input type="checkbox" checked={aktivert} onChange={e => setAktivert(e.target.checked)} className="accent-[#D76428]" />
        {t('Enabled')}
      </label>

      <div className="space-y-2">
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('InfluxDB URL')}</label>
          <input className={felt} value={url} onChange={e => setUrl(e.target.value)} placeholder="http://192.168.1.22:8086" />
        </div>
        <div className="flex gap-2">
          <div className="flex-1">
            <label className="block text-xs text-gray-500 mb-1">{t('Organization')}</label>
            <input className={felt} value={org} onChange={e => setOrg(e.target.value)} placeholder="Sundet" />
          </div>
          <div className="flex-1">
            <label className="block text-xs text-gray-500 mb-1">{t('Bucket')}</label>
            <input className={felt} value={bucket} onChange={e => setBucket(e.target.value)} placeholder="Strøm" />
          </div>
          <div className="w-24">
            <label className="block text-xs text-gray-500 mb-1">{t('Interval (s)')}</label>
            <input className={felt} type="number" min={2} value={intervall} onChange={e => setIntervall(Number(e.target.value))} />
          </div>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">
            {t('Write token')} <span className="text-gray-400">({tokenSatt ? t('set — leave blank to keep') : t('required')})</span>
          </label>
          <input className={felt} type="password" value={token} onChange={e => setToken(e.target.value)} placeholder={tokenSatt ? '••••••••' : ''} autoComplete="new-password" />
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
