import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchPqzip, lagrePqzip, synkPqzip } from '../api/pqzip'
import type { PqzipKonfig } from '../api/pqzip'
import Panel from './ui/Panel'
import { useI18n } from '../i18n'

/**
 * PQZIP-arkivering: last ned instrumentet sine .PQZip-bølgjeform-filer med
 * retensjon. PQZIP er Elspec sitt lukka format — dette er fil-arkiv for
 * PQSCADA, ikkje live kanalar.
 */
export default function PqzipArkivCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchPqzip(), [])
  const { data, refresh } = usePolling<PqzipKonfig>(fetcher, 5000)
  const [u, setU] = useState<Partial<PqzipKonfig>>({})
  const [melding, setMelding] = useState<string | null>(null)
  const [jobbar, setJobbar] = useState(false)

  const v = <K extends keyof PqzipKonfig>(f: K): PqzipKonfig[K] =>
    (u[f] !== undefined ? (u[f] as PqzipKonfig[K]) : data?.[f]) as PqzipKonfig[K]
  const sett = (f: string, val: unknown) => setU((o) => ({ ...o, [f]: val }))
  const lagre = async () => {
    setMelding(null)
    try { const r = await lagrePqzip(u); setMelding(r.melding); setU({}); refresh() }
    catch (e) { setMelding(e instanceof Error ? e.message : String(e)) }
  }
  const synkNo = async () => {
    setJobbar(true); setMelding(null)
    try { const r = await synkPqzip(); setMelding(r.melding); refresh() }
    catch (e) { setMelding(e instanceof Error ? e.message : String(e)) }
    finally { setJobbar(false) }
  }
  const inn = 'ui-input'
  const s = data?.status

  return (
    <Panel kicker={t('Archiving')} title={t('PQZIP archiving')}
      sub={t('Downloads the instrument’s .PQZip waveform files with retention. PQZIP is Elspec’s closed format — this is a file archive for PQSCADA, not live channels.')}>
      {melding && <div className="mb-3 p-2 text-sm" style={{ background: 'var(--color-accent-100)', color: 'var(--color-accent-800)' }}>{melding}</div>}
      <div className="grid grid-cols-2 gap-2 mb-2">
        <label className="flex items-center gap-2 text-sm col-span-2">
          <input type="checkbox" checked={!!v('aktivert')} onChange={(e) => sett('aktivert', e.target.checked)} />
          {t('Archive automatically')}
        </label>
        <div className="col-span-2"><label className="ui-label">{t('Remote folder')}</label>
          <input className={inn} value={v('rot') ?? ''} placeholder="/CF_UPMB/PQZIPDATA_"
            onChange={(e) => sett('rot', e.target.value)} /></div>
        <div><label className="ui-label">{t('File pattern')}</label>
          <input className={inn} value={v('monster') ?? ''} placeholder="*.PQZip"
            onChange={(e) => sett('monster', e.target.value)} /></div>
        <div><label className="ui-label">{t('Interval (minutes)')}</label>
          <input className={inn} type="number" min={1} value={v('intervall_min') ?? 15}
            onChange={(e) => sett('intervall_min', Number(e.target.value))} /></div>
        <div className="col-span-2"><label className="ui-label">{t('Target folder (NAS recommended)')}</label>
          <input className={inn} value={v('maalkatalog') ?? ''} placeholder="/data/nas/pqzip"
            onChange={(e) => sett('maalkatalog', e.target.value)} /></div>
        <div><label className="ui-label">{t('Keep (days)')}</label>
          <input className={inn} type="number" min={0} value={v('retensjon_dagar') ?? 14}
            onChange={(e) => sett('retensjon_dagar', Number(e.target.value))} /></div>
        <div><label className="ui-label">{t('Max size (MB)')}</label>
          <input className={inn} type="number" min={50} value={v('maks_mb') ?? 2000}
            onChange={(e) => sett('maks_mb', Number(e.target.value))} /></div>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <button className="btn-primary" onClick={lagre}>{t('Save')}</button>
        <button className="btn-ghost" onClick={synkNo} disabled={jobbar}>{jobbar ? t('Archiving…') : t('Archive now')}</button>
        {s && (
          <span className="hint ml-auto">
            {s.tilstand || t('idle')} · {s.lokalt_tal} {t('files')} · {s.lokalt_mb} MB
            {s.melding ? ` — ${s.melding}` : ''}
          </span>
        )}
      </div>
    </Panel>
  )
}
