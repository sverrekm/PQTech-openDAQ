import { useState, useEffect, useCallback } from 'react'
import {
  fetchHubLagerKonfig, lagreHubLagerKonfig, hubLagerCsvUrl,
} from '../api/hubLager'
import type { HubLagerKonfig } from '../api/hubLager'
import { useI18n } from '../i18n'

/** Hub-lager: persistent lagring av kanaldata som nodane pushar til hubben.
 *  Lagrar til SQLite på hubben, med retensjon, nedsampling og CSV-eksport. */
export default function HubStorageCard() {
  const { t } = useI18n()
  const [k, setK] = useState<HubLagerKonfig>({
    aktivert: false, db_sti: '/data/maalinger/hub_kanaldata.db',
    retensjon_dagar: 30, min_intervall_s: 1, maks_mb: 0,
  })
  const [busy, setBusy] = useState(false)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  const last = useCallback(() => {
    fetchHubLagerKonfig().then(setK).catch(() => {})
  }, [])
  useEffect(() => {
    last()
    const id = setInterval(last, 10000)
    return () => clearInterval(id)
  }, [last])

  const sett = (felt: keyof HubLagerKonfig, v: number | boolean | string) =>
    setK(p => ({ ...p, [felt]: v }))

  const lagre = async () => {
    setBusy(true); setMelding(null)
    try {
      await lagreHubLagerKonfig({
        aktivert: k.aktivert, db_sti: k.db_sti,
        retensjon_dagar: k.retensjon_dagar,
        min_intervall_s: k.min_intervall_s, maks_mb: k.maks_mb,
      })
      setMelding({ text: t('Saved.'), ok: true })
      last()
    } catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy(false)
  }

  const felt = "w-full text-sm px-3 py-1.5 rounded-md border border-gray-300 focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"

  const fmtTs = (ts?: number | null) =>
    ts ? new Date(ts).toLocaleString() : '–'

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">{t('Hub data storage')}</h2>
      <p className="text-sm text-gray-500 -mt-1 mb-4 leading-snug">{t('Persist channel data pushed from nodes to a database on the hub. Survives restarts; export to CSV.')}</p>

      <label className="flex items-center gap-2 mb-3 text-sm text-gray-700">
        <input type="checkbox" checked={k.aktivert} onChange={e => sett('aktivert', e.target.checked)} className="accent-[#D76428]" />
        {t('Enabled')}
      </label>

      <div className="flex flex-wrap gap-2">
        <div className="w-full">
          <label className="block text-xs text-gray-500 mb-1">{t('Database path')}</label>
          <input className={felt} type="text" value={k.db_sti} onChange={e => sett('db_sti', e.target.value)} />
        </div>
        <div className="w-32">
          <label className="block text-xs text-gray-500 mb-1">{t('Retention (days)')}</label>
          <input className={felt} type="number" min={0} value={k.retensjon_dagar} onChange={e => sett('retensjon_dagar', Number(e.target.value))} />
        </div>
        <div className="w-36">
          <label className="block text-xs text-gray-500 mb-1">{t('Min interval (s)')}</label>
          <input className={felt} type="number" min={0} step={0.1} value={k.min_intervall_s} onChange={e => sett('min_intervall_s', Number(e.target.value))} />
        </div>
        <div className="w-32">
          <label className="block text-xs text-gray-500 mb-1">{t('Max size (MB)')}</label>
          <input className={felt} type="number" min={0} value={k.maks_mb} onChange={e => sett('maks_mb', Number(e.target.value))} />
        </div>
      </div>

      <div className="flex gap-2 mt-3">
        <button onClick={lagre} disabled={busy} className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50">
          {busy ? t('Saving...') : t('Save')}
        </button>
        <a href={hubLagerCsvUrl()} className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-1.5 px-3 rounded-lg text-sm">
          {t('Download CSV')}
        </a>
      </div>

      {melding && (
        <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${melding.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
          {melding.text}
        </div>
      )}

      {/* Status */}
      <div className="mt-4 grid grid-cols-3 gap-2 text-center">
        <div className="bg-gray-50 rounded-lg py-2">
          <div className="text-lg font-semibold text-gray-800">{(k.rader ?? 0).toLocaleString()}</div>
          <div className="text-xs text-gray-500">{t('Rows')}</div>
        </div>
        <div className="bg-gray-50 rounded-lg py-2">
          <div className="text-lg font-semibold text-gray-800">{(k.storleik_mb ?? 0)} MB</div>
          <div className="text-xs text-gray-500">{t('DB size')}</div>
        </div>
        <div className="bg-gray-50 rounded-lg py-2">
          <div className="text-lg font-semibold text-gray-800">{k.kø_lengd ?? 0}</div>
          <div className="text-xs text-gray-500">{t('Queue')}</div>
        </div>
      </div>

      {k.nodar && k.nodar.length > 0 && (
        <table className="w-full text-sm mt-3">
          <thead>
            <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
              <th className="py-1 font-medium">{t('Node')}</th>
              <th className="py-1 font-medium text-right">{t('Rows')}</th>
              <th className="py-1 font-medium text-right">{t('Last')}</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody>
            {k.nodar.map(n => (
              <tr key={n.node_id} className="border-b border-gray-100">
                <td className="py-1 text-gray-800">{n.node_namn || n.node_id}</td>
                <td className="py-1 text-right text-gray-600">{n.rader.toLocaleString()}</td>
                <td className="py-1 text-right text-gray-500 text-xs">{fmtTs(n.siste_ts)}</td>
                <td className="py-1 text-right">
                  <a href={hubLagerCsvUrl(n.node_id)} className="text-[#D76428] hover:underline text-xs">CSV</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {k.siste_feil ? (
        <div className="mt-3 px-3 py-2 rounded-lg text-sm bg-red-50 text-red-700">{k.siste_feil}</div>
      ) : null}
    </div>
  )
}
