import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchPqubeHent, lagrePqubeHent, hentPqubeNo } from '../api/pqubehent'
import type { PqubeHentKonfig } from '../api/pqubehent'
import { useI18n } from '../i18n'
import { useToast } from './Toast'
import Panel from './ui/Panel'

/**
 * HTTP-henting av PQube 3 event-tre. Noden blar PQubens web (katalog-lister)
 * og drar ned event-filer (PQDIF/CSV/bølgeform) til arkivet + CSV → kanalar.
 * Meir påliteleg enn FTP-push; får med eksisterande hendingar òg.
 */
export default function PqubeHentCard() {
  const { t } = useI18n()
  const toast = useToast()
  const fetcher = useCallback(() => fetchPqubeHent(), [])
  const { data, refresh } = usePolling<PqubeHentKonfig>(fetcher, 5000)
  const [utkast, setUtkast] = useState<Partial<PqubeHentKonfig> & { passord?: string }>({})
  const [jobbar, setJobbar] = useState<string | null>(null)

  const v = <K extends keyof PqubeHentKonfig>(felt: K): PqubeHentKonfig[K] =>
    (utkast[felt] !== undefined ? utkast[felt] : data?.[felt]) as PqubeHentKonfig[K]
  const sett = (felt: string, verdi: unknown) => setUtkast(u => ({ ...u, [felt]: verdi }))

  const lagre = async () => {
    setJobbar('lagre')
    try {
      const r = await lagrePqubeHent(utkast)
      if (r.suksess) { toast.suksess(r.melding); setUtkast({}) } else toast.feil(r.melding)
      refresh()
    } catch (e) { toast.feil(`${t('Error')}: ${e}`) }
    setJobbar(null)
  }
  const hentNo = async () => {
    setJobbar('hent')
    try {
      const r = await hentPqubeNo()
      if (r.suksess) toast.suksess(r.melding); else toast.feil(r.melding)
      refresh()
    } catch (e) { toast.feil(`${t('Error')}: ${e}`) }
    setJobbar(null)
  }

  const st = data?.status
  const inn = 'ui-input'

  return (
    <Panel
      kicker={t('Retrieval')}
      title={t('PQube event fetch (HTTP)')}
      sub={t('The node browses the PQube’s web directory and pulls event files (PQDIF waveforms, CSV) into the archive. More reliable than FTP push — also captures events already on the PQube.')}
      right={
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" className="rounded" checked={!!v('aktivert')} disabled={jobbar !== null}
            onChange={e => { sett('aktivert', e.target.checked) }} />
          {v('aktivert') ? t('Enabled') : t('Disabled')}
        </label>
      }
    >
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div>
          <label className="ui-label">{t('PQube host (IP)')}</label>
          <input className={inn} value={v('vert') ?? ''} placeholder="192.168.1.204"
            onChange={e => sett('vert', e.target.value)} />
        </div>
        <div>
          <label className="ui-label">{t('Interval (minutes)')}</label>
          <input className={inn} type="number" min={1} value={v('intervall_min') ?? 10}
            onChange={e => sett('intervall_min', Number(e.target.value))} />
        </div>
        <div>
          <label className="ui-label">{t('Fetch events newer than (days)')}</label>
          <input className={inn} type="number" min={1} value={v('dagar_tilbake') ?? 14}
            onChange={e => sett('dagar_tilbake', Number(e.target.value))} />
        </div>
        <div>
          <label className="ui-label">{t('Customer (optional)')}</label>
          <input className={inn} value={v('kunde') ?? ''}
            onChange={e => sett('kunde', e.target.value)} />
        </div>
        <div>
          <label className="ui-label">{t('Channel prefix (optional)')}</label>
          <input className={inn} value={v('kanal_prefiks') ?? ''} placeholder="PQ2_"
            onChange={e => sett('kanal_prefiks', e.target.value)} />
        </div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" className="rounded" checked={!!v('hent_gif')}
              onChange={e => sett('hent_gif', e.target.checked)} />
            {t('Also fetch graph GIFs')}
          </label>
        </div>
      </div>

      <div className="flex gap-2 flex-wrap items-center">
        <button className="btn-primary" onClick={lagre} disabled={jobbar === 'lagre'}>
          {jobbar === 'lagre' ? t('Saving...') : t('Save')}
        </button>
        <button className="btn-ghost" onClick={hentNo} disabled={jobbar === 'hent' || !data?.aktivert}>
          {jobbar === 'hent' ? t('Fetching…') : t('Fetch now')}
        </button>
        {st && (
          <span className="text-xs text-gray-500">
            <span className={st.tilstand === 'ok' ? 'text-green-600' : st.tilstand === 'feil' ? 'text-red-600' : 'text-[#D76428]'}>
              {st.tilstand || t('idle')}
            </span>
            {st.melding ? ` — ${st.melding}` : ''}
            {` · ${t('fetched total')}: ${st.henta_totalt}`}
            {st.sist_fil ? ` · ${t('last')}: ${st.sist_fil}` : ''}
          </span>
        )}
      </div>
    </Panel>
  )
}
