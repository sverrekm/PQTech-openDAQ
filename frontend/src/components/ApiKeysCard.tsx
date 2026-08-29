import { useState, useEffect } from 'react'
import {
  fetchApiNokler, opprettApiNokkel, settApiNokkelAktivert, slettApiNokkel,
} from '../api/apiNokler'
import type { ApiNokkel } from '../api/apiNokler'
import CopyableCommand from './CopyableCommand'
import { useI18n } from '../i18n'

/** API-nøklar for eksterne lese-klientar (desktop-widget o.l. på anna nett).
 *  Nøkkelen vert vist éin gong ved oppretting — vi kan ikkje hente han fram
 *  att, sidan berre hashen er lagra. */
export default function ApiKeysCard() {
  const { t } = useI18n()
  const [nokler, setNokler] = useState<ApiNokkel[]>([])
  const [namn, setNamn] = useState('')
  const [utloep, setUtloep] = useState('')
  const [filter, setFilter] = useState('')
  const [nyNokkel, setNyNokkel] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  const last = () => {
    fetchApiNokler().then(r => setNokler(r.nokler || [])).catch(() => {})
  }
  useEffect(last, [])

  const opprett = async () => {
    if (!namn.trim()) {
      setMelding({ text: t('Give the key a name so you can recognise it later.'), ok: false })
      return
    }
    setBusy(true); setMelding(null)
    try {
      const res = await opprettApiNokkel({
        namn: namn.trim(),
        utloep: utloep || undefined,
        kanal_filter: filter.split(',').map(s => s.trim()).filter(Boolean),
      })
      setNyNokkel(res.nokkel)
      setNamn(''); setUtloep(''); setFilter('')
      last()
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
    setBusy(false)
  }

  const veksle = async (n: ApiNokkel) => {
    try {
      await settApiNokkelAktivert(n.id, !n.aktivert)
      last()
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
  }

  const slett = async (n: ApiNokkel) => {
    if (!confirm(t('Revoke this key? Clients using it stop working immediately.'))) return
    try {
      await slettApiNokkel(n.id)
      last()
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
  }

  const felt = "w-full text-sm px-3 py-1.5 rounded-md border border-gray-300 focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
  const vertsnamn = typeof window !== 'undefined' ? window.location.origin : ''

  const status = (n: ApiNokkel) => {
    const utgaatt = n.utloep && new Date(n.utloep) < new Date()
    if (utgaatt) return { tekst: t('Expired'), klasse: 'bg-gray-100 text-gray-500' }
    if (!n.aktivert) return { tekst: t('Disabled'), klasse: 'bg-gray-100 text-gray-500' }
    return { tekst: t('Active'), klasse: 'bg-green-100 text-green-700' }
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">{t('API keys (external read access)')}</h2>
      <p className="text-sm text-gray-500 -mt-1 mb-4 leading-snug">
        {t('Let a client outside this network — for example a desktop widget — read measurement values over HTTPS. Keys are read-only and can be revoked one by one.')}
      </p>

      {nyNokkel && (
        <div className="border border-[#D76428] bg-orange-50 rounded-lg p-3 mb-4">
          <p className="text-sm font-semibold text-[#D76428] mb-2">
            {t('Copy the key now — it is shown only once.')}
          </p>
          <CopyableCommand text={nyNokkel} />
          <p className="text-xs text-gray-600 mt-2 mb-1">{t('Test it:')}</p>
          <CopyableCommand text={`curl -H "X-API-Key: ${nyNokkel}" ${vertsnamn}/api/v1/kanalar`} />
          <button
            className="mt-3 text-xs text-gray-600 underline cursor-pointer bg-transparent border-none p-0"
            onClick={() => setNyNokkel(null)}
          >
            {t('I have saved it')}
          </button>
        </div>
      )}

      <div className="space-y-2 mb-3">
        <input className={felt} placeholder={t('Name (e.g. "Desktop widget, office")')}
               value={namn} onChange={e => setNamn(e.target.value)} />
        <div className="flex gap-2">
          <label className="flex-1 text-xs text-gray-500">
            {t('Expires (optional)')}
            <input type="date" className={felt} value={utloep} onChange={e => setUtloep(e.target.value)} />
          </label>
          <label className="flex-1 text-xs text-gray-500">
            {t('Channels (optional, comma separated)')}
            <input className={felt} placeholder="Sundet/*, Straum L1"
                   value={filter} onChange={e => setFilter(e.target.value)} />
          </label>
        </div>
      </div>

      <button
        className="bg-[#D76428] text-white text-sm px-4 py-1.5 rounded-md hover:bg-[#c25a24] disabled:opacity-50"
        onClick={opprett} disabled={busy}
      >
        {busy ? t('Creating...') : t('Create key')}
      </button>

      {melding && (
        <p className={`text-sm mt-3 ${melding.ok ? 'text-green-700' : 'text-red-600'}`}>{melding.text}</p>
      )}

      {nokler.length > 0 && (
        <table className="w-full text-sm mt-4">
          <thead>
            <tr className="text-xs text-gray-500 uppercase tracking-wider text-left">
              <th className="py-1 font-semibold">{t('Name')}</th>
              <th className="py-1 font-semibold">{t('Key')}</th>
              <th className="py-1 font-semibold">{t('Last used')}</th>
              <th className="py-1 font-semibold">{t('Status')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {nokler.map(n => {
              const s = status(n)
              return (
                <tr key={n.id} className="border-t border-gray-100">
                  <td className="py-1.5">
                    {n.namn}
                    {n.kanal_filter.length > 0 && (
                      <span className="block text-xs text-gray-500">{n.kanal_filter.join(', ')}</span>
                    )}
                  </td>
                  <td className="py-1.5 font-mono text-xs text-gray-500">{n.prefiks}…</td>
                  <td className="py-1.5 text-xs text-gray-500">{n.sist_brukt || t('Never')}</td>
                  <td className="py-1.5">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${s.klasse}`}>{s.tekst}</span>
                  </td>
                  <td className="py-1.5 text-right whitespace-nowrap">
                    <button className="text-xs text-gray-600 underline bg-transparent border-none cursor-pointer mr-3"
                            onClick={() => veksle(n)}>
                      {n.aktivert ? t('Disable') : t('Enable')}
                    </button>
                    <button className="text-xs text-red-600 underline bg-transparent border-none cursor-pointer"
                            onClick={() => slett(n)}>
                      {t('Revoke')}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
