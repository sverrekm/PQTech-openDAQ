import { useState, useEffect } from 'react'
import { fetchPushKonfig, oppdaterPushKonfig } from '../api/push'
import type { PushKonfig } from '../api/push'
import { useI18n } from '../i18n'
import Panel from './ui/Panel'

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
    <Panel
      kicker={t('Identity')}
      title={t('Name this box')}
      sub={t('A clear name for this box (node or hub). It tags all measurements from here, so you can see the source in Grafana, the hub and reports.')}
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex-1 min-w-[200px]">
          <label className="ui-label">{t('Name')}</label>
          <input
            type="text"
            className="ui-input"
            value={namn}
            onChange={e => setNamn(e.target.value)}
            placeholder={t('e.g. Sundet, Tavle 3, Kunde A – hovudtavle')}
          />
        </div>
        <button onClick={lagre} disabled={busy || !konfig} className="btn-primary">
          {busy ? t('Saving...') : t('Save')}
        </button>
      </div>

      {melding && (
        <div className={`mt-3 px-3 py-2 rounded text-sm ${melding.ok ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-red-50 text-red-800 border border-red-200'}`}>
          {melding.text}
        </div>
      )}
    </Panel>
  )
}
