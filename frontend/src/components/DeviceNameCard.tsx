import { useState, useEffect } from 'react'
import { fetchPushKonfig, oppdaterPushKonfig } from '../api/push'
import type { PushKonfig } from '../api/push'
import { useI18n } from '../i18n'

/** Enhetsnamn: namnet som identifiserer denne boksen (node/hub) og taggar
 *  alle målingane (Grafana, hub, rapportar). Read-modify-write mot push-
 *  konfig så parent-/token-innstillingar ikkje vert overskrivne. */
export default function DeviceNameCard() {
  const { t } = useI18n()
  const [konfig, setKonfig] = useState<PushKonfig | null>(null)
  const [namn, setNamn] = useState('')
  const [busy, setBusy] = useState(false)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => {
    fetchPushKonfig().then(k => { setKonfig(k); setNamn(k.node_namn || '') }).catch(() => {})
  }, [])

  const lagre = async () => {
    if (!konfig) return
    setBusy(true); setMelding(null)
    try {
      const r = await oppdaterPushKonfig({ ...konfig, node_namn: namn.trim() })
      setMelding({ text: r.melding || t('Saved.'), ok: r.suksess })
    } catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy(false)
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">{t('Device name')}</h2>
      <p className="text-sm text-gray-500 -mt-1 mb-4 leading-snug">{t('A clear name for this box (node or hub). It tags all measurements from here, so you can see the source in Grafana, the hub and reports.')}</p>

      <div className="flex flex-wrap items-end gap-2">
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('Name')}</label>
          <input
            type="text"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
            value={namn}
            onChange={e => setNamn(e.target.value)}
            placeholder={t('e.g. Sundet, Tavle 3, Kunde A – hovudtavle')}
          />
        </div>
        <button
          onClick={lagre}
          disabled={busy || !konfig}
          className="px-4 py-2 bg-[#D76428] text-white text-sm font-medium rounded-md hover:bg-[#c0571f] disabled:opacity-50 transition-colors"
        >
          {busy ? t('Saving...') : t('Save')}
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
