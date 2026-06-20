import { useState, useEffect, useCallback } from 'react'
import { fetchRaaFilKonfig, lagreRaaFilKonfig } from '../api/raaFil'
import type { RaaFilKonfig } from '../api/raaFil'
import { useI18n } from '../i18n'

/** Rå-fil-arkiv: skriv måledata som CSV-filer til ein valfri katalog (typisk
 *  ein NAS montert via CIFS/SMB). Langtidsarkiv ved sida av InfluxDB. */
export default function RawArchiveCard() {
  const { t } = useI18n()
  const [k, setK] = useState<RaaFilKonfig>({ aktivert: false, katalog: '/data/nas/maalingar' })
  const [busy, setBusy] = useState(false)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  const last = useCallback(() => { fetchRaaFilKonfig().then(setK).catch(() => {}) }, [])
  useEffect(() => {
    last()
    const id = setInterval(last, 10000)
    return () => clearInterval(id)
  }, [last])

  const sett = (felt: keyof RaaFilKonfig, v: boolean | string) =>
    setK(p => ({ ...p, [felt]: v }))

  const lagre = async () => {
    setBusy(true); setMelding(null)
    try {
      await lagreRaaFilKonfig({ aktivert: k.aktivert, katalog: k.katalog })
      setMelding({ text: t('Saved.'), ok: true })
      last()
    } catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy(false)
  }

  const felt = "w-full text-sm px-3 py-1.5 rounded-md border border-gray-300 focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
  const fmtTs = (ts?: number) => ts ? new Date(ts * 1000).toLocaleString() : '–'

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">{t('Raw file archive (NAS)')}</h2>
      <p className="text-sm text-gray-500 -mt-1 mb-4 leading-snug">{t('Archive measurement data as CSV files to a folder — typically a NAS mounted via CIFS/SMB. Long-term storage alongside InfluxDB.')}</p>

      <label className="flex items-center gap-2 mb-3 text-sm text-gray-700">
        <input type="checkbox" checked={k.aktivert} onChange={e => sett('aktivert', e.target.checked)} className="accent-[#D76428]" />
        {t('Enabled')}
      </label>

      <div className="mb-2">
        <label className="block text-xs text-gray-500 mb-1">{t('Archive folder (in container)')}</label>
        <input className={felt} type="text" value={k.katalog} onChange={e => sett('katalog', e.target.value)} placeholder="/data/nas/maalingar" />
        <p className="text-[11px] text-gray-400 mt-1">{t('Mount the NAS on the host and set NAS_DIR via pqtech-config.sh; it appears at /data/nas in the container.')}</p>
      </div>

      <div className="flex gap-2 mt-3">
        <button onClick={lagre} disabled={busy} className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50">
          {busy ? t('Saving...') : t('Save')}
        </button>
      </div>

      {melding && (
        <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${melding.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
          {melding.text}
        </div>
      )}

      {/* Status */}
      <div className="mt-4 grid grid-cols-3 gap-2 text-center">
        <div className="bg-gray-50 rounded-lg py-2">
          <div className="text-lg font-semibold text-gray-800">{(k.skrive_totalt ?? 0).toLocaleString()}</div>
          <div className="text-xs text-gray-500">{t('Rows written')}</div>
        </div>
        <div className="bg-gray-50 rounded-lg py-2">
          <div className="text-lg font-semibold text-gray-800">{k.kø_lengd ?? 0}</div>
          <div className="text-xs text-gray-500">{t('Queue')}</div>
        </div>
        <div className="bg-gray-50 rounded-lg py-2">
          <div className={`text-lg font-semibold ${k.skrivbar ? 'text-green-700' : 'text-red-600'}`}>{k.skrivbar ? '✓' : '✗'}</div>
          <div className="text-xs text-gray-500">{t('Writable')}</div>
        </div>
      </div>
      <div className="mt-2 text-xs text-gray-400">{t('Last write')}: {fmtTs(k.siste_skriv_ts)}</div>

      {k.siste_feil ? (
        <div className="mt-2 px-3 py-2 rounded-lg text-sm bg-red-50 text-red-700">{k.siste_feil}</div>
      ) : null}
    </div>
  )
}
