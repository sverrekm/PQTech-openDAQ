import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchInnboks, lagreInnboks } from '../api/innboks'
import type { InnboksKonfig } from '../api/innboks'
import { useI18n } from '../i18n'
import { useToast } from './Toast'
import Panel from './ui/Panel'

/**
 * SFTP-innboks for instrument som PUSHAR (t.d. PQube 3 utan FTP-server).
 * Slår på ein låst sftp-brukar på noden og viser tilkoblingsdetaljane du skal
 * skrive inn på instrumentet. Mottekne filer arkiverast + CSV → kanalar.
 */
export default function InstrumentInnboksCard() {
  const { t } = useI18n()
  const toast = useToast()
  const fetcher = useCallback(() => fetchInnboks(), [])
  const { data, refresh } = usePolling<InnboksKonfig>(fetcher, 5000)
  const [busy, setBusy] = useState(false)
  const [visPassord, setVisPassord] = useState(false)

  const kall = async (endring: Parameters<typeof lagreInnboks>[0], melding: string) => {
    setBusy(true)
    try {
      const r = await lagreInnboks(endring)
      if (r.suksess) toast.suksess(melding)
      else toast.feil(r.melding)
      refresh()
    } catch (e) { toast.feil(`${t('Error')}: ${e}`) }
    setBusy(false)
  }

  const kopier = (v: string) => {
    try { navigator.clipboard?.writeText(v); toast.info(t('Copied')) } catch { /* ignore */ }
  }

  const aktiv = !!data?.aktivert
  const st = data?.status
  const felt = 'flex items-center justify-between gap-2 py-1 border-b border-gray-100 last:border-0'
  const verdi = 'font-mono text-sm text-gray-800 truncate'

  return (
    <Panel
      kicker={t('Retrieval')}
      title={t('FTP inbox (instrument push)')}
      sub={t('For instruments that push their files (e.g. PQube 3 FTP push on events). The node runs a locked FTP server, receives the files, archives them and parses CSV into channels.')}
      right={
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" className="rounded" checked={aktiv} disabled={busy}
            onChange={(e) => kall({ aktivert: e.target.checked },
              e.target.checked ? t('FTP inbox enabled') : t('FTP inbox disabled'))} />
          {aktiv ? t('Enabled') : t('Disabled')}
        </label>
      }
    >
      {!aktiv ? (
        <div className="hint">{t('Enable to start a locked FTP server and show the connection details to enter on the instrument.')}</div>
      ) : (
        <>
          <div className="p-2 mb-3 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800">
            {t('Enter these on the instrument’s FTP-push settings. Only reachable on the local network.')}
          </div>
          <div className="border border-gray-200 rounded-lg px-3 py-1.5 mb-3">
            <div className={felt}>
              <span className="ui-label">{t('Host (node IP)')}</span>
              <span className={verdi}>{data?.vert || '—'}
                <button className="ml-2 text-[#D76428] hover:underline text-xs" onClick={() => kopier(data?.vert || '')}>{t('Copy')}</button>
              </span>
            </div>
            <div className={felt}>
              <span className="ui-label">{t('Port')}</span><span className={verdi}>{data?.port ?? 21} (FTP)</span>
            </div>
            <div className={felt}>
              <span className="ui-label">{t('Username')}</span>
              <span className={verdi}>{data?.brukar}
                <button className="ml-2 text-[#D76428] hover:underline text-xs" onClick={() => kopier(data?.brukar || '')}>{t('Copy')}</button>
              </span>
            </div>
            <div className={felt}>
              <span className="ui-label">{t('Password')}</span>
              <span className={verdi}>
                {visPassord ? data?.passord : '•'.repeat(Math.min(16, (data?.passord || '').length))}
                <button className="ml-2 text-[#D76428] hover:underline text-xs" onClick={() => setVisPassord(v => !v)}>{visPassord ? t('Hide') : t('Show')}</button>
                <button className="ml-2 text-[#D76428] hover:underline text-xs" onClick={() => kopier(data?.passord || '')}>{t('Copy')}</button>
              </span>
            </div>
            <div className={felt}>
              <span className="ui-label">{t('Remote path')}</span><span className={verdi}>{data?.fjern_sti || '/opplasting'}</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 mb-3">
            <div>
              <label className="ui-label">{t('Customer (optional)')}</label>
              <input className="ui-input" defaultValue={data?.kunde || ''}
                onBlur={(e) => { if (e.target.value !== (data?.kunde || '')) kall({ kunde: e.target.value }, t('Saved.')) }} />
            </div>
            <div>
              <label className="ui-label">{t('Channel prefix (optional)')}</label>
              <input className="ui-input" defaultValue={data?.kanal_prefiks || ''} placeholder="PQ2_"
                onBlur={(e) => { if (e.target.value !== (data?.kanal_prefiks || '')) kall({ kanal_prefiks: e.target.value }, t('Saved.')) }} />
            </div>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <button className="btn-ghost text-red-600 border-red-200 hover:bg-red-50"
              disabled={busy}
              onClick={() => { if (window.confirm(t('Generate a new password? You must update the instrument with the new one.'))) kall({ regenerer_passord: true }, t('New password generated')) }}>
              {t('Regenerate password')}
            </button>
            {st && (
              <span className="text-xs text-gray-500">
                {t('Received')}: {st.mottatt_totalt}
                {st.sist_fil ? ` · ${t('last')}: ${st.sist_fil}` : ''}
              </span>
            )}
          </div>
        </>
      )}
    </Panel>
  )
}
