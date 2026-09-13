import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import {
  fetchSmtp, lagreSmtp, fetchSmtpMeldingar, fetchNtp, lagreNtp,
  fetchFtpProxy, lagreFtpProxy,
} from '../api/tjenester'
import type { SmtpKonfig, SmtpMelding, NtpKonfig, FtpProxyKonfig } from '../api/tjenester'
import Panel from './ui/Panel'
import { useI18n } from '../i18n'

/**
 * Node-tenester: SMTP-mottakar og NTP-tidsserver.
 *
 * Instrument på eit isolert målenett mailar rapportar/varsel (SMTP-klient)
 * og treng rett klokke (NTP). Noden står på same nett og kan vere begge
 * delar. SMTP-CSV-vedlegg blir kanalar via FTP-pipelinen.
 */
export default function NodeTjenesterCard() {
  const { t } = useI18n()

  return (
    <Panel kicker={t('Node services')} title={t('Server roles')}
      sub={t('Let this node serve the instruments on its network: receive their emailed reports, and hand out the correct time.')}>
      <Smtp />
      <div className="my-4" style={{ borderTop: '1px solid var(--color-line-soft)' }} />
      <Ntp />
      <div className="my-4" style={{ borderTop: '1px solid var(--color-line-soft)' }} />
      <FtpProxy />
    </Panel>
  )
}

function FtpProxy() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchFtpProxy(), [])
  const { data, refresh } = usePolling<FtpProxyKonfig>(fetcher, 5000)
  const [u, setU] = useState<Partial<FtpProxyKonfig>>({})
  const [melding, setMelding] = useState<string | null>(null)
  const v = <K extends keyof FtpProxyKonfig>(f: K): FtpProxyKonfig[K] =>
    (u[f] !== undefined ? (u[f] as FtpProxyKonfig[K]) : data?.[f]) as FtpProxyKonfig[K]
  const sett = (f: string, val: unknown) => setU((o) => ({ ...o, [f]: val }))
  const lagre = async () => {
    setMelding(null)
    try { const r = await lagreFtpProxy(u); setMelding(r.melding); setU({}); refresh() }
    catch (e) { setMelding(e instanceof Error ? e.message : String(e)) }
  }
  const naaAdr = data?.tailscale_ip && data?.lytt_port
    ? `${data.tailscale_ip}:${data.lytt_port}` : null

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className="card-head" style={{ margin: 0 }}>{t('Instrument FTP over Tailscale')}</span>
        <span className="tag ml-auto" style={{ color: st(data?.status?.tilstand), border: '1px solid var(--color-divider)' }}>
          {data?.status?.tilstand || '—'}{data?.status?.totalt_okter ? ` · ${data.status.totalt_okter} ${t('sessions')}` : ''}
        </span>
      </div>
      <p className="hint mb-2">{t('Makes the instrument’s FTP reachable from the hub side over Tailscale — PASV is rewritten so file transfers work through the NAT.')}</p>
      <div className="grid grid-cols-2 gap-2 mb-2">
        <label className="flex items-center gap-2 text-sm col-span-2">
          <input type="checkbox" checked={!!v('aktivert')} onChange={(e) => sett('aktivert', e.target.checked)} />
          {t('Enable FTP proxy')}
        </label>
        <div><label className="ui-label">{t('Listen port')}</label>
          <input className={inn} type="number" value={v('lytt_port') ?? 2121} onChange={(e) => sett('lytt_port', Number(e.target.value))} /></div>
        <div className="grid grid-cols-3 gap-1">
          <div className="col-span-2"><label className="ui-label">{t('Target FTP (IP)')}</label>
            <input className={inn} value={v('maal_vert') ?? ''} placeholder={data?.maal_vert_effektiv || '10.99.0.1'}
              onChange={(e) => sett('maal_vert', e.target.value)} /></div>
          <div><label className="ui-label">{t('Port')}</label>
            <input className={inn} type="number" value={v('maal_port') ?? 21} onChange={(e) => sett('maal_port', Number(e.target.value))} /></div>
        </div>
        <div className="col-span-2">
          <label className="ui-label">{t('Bind address (blank = Tailscale only)')}</label>
          <input className={inn} value={v('bind_ip') ?? ''} placeholder={t('e.g. hub office-LAN IP, or blank')}
            onChange={(e) => sett('bind_ip', e.target.value)} list="ftpproxy-ips" />
          <datalist id="ftpproxy-ips">
            {(data?.lokale_ip ?? []).map((ip) => <option key={ip} value={ip} />)}
          </datalist>
          <p className="hint mt-1">
            {t('On the hub, set this to its office-LAN IP and Target to the node’s Tailscale FTP (e.g. 100.79.202.65:2121) to relay it onto the office network.')}
            {(data?.lokale_ip?.length ?? 0) > 0 && ` ${t('This host:')} ${data!.lokale_ip!.join(', ')}`}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <button className="btn-primary" onClick={lagre}>{t('Save')}</button>
        {naaAdr && data?.status?.tilstand === 'koeyrer' && (
          <span className="hint">{t('Connect an FTP client to')} <span className="ui-num">{naaAdr}</span> ({t('user')} ftpuser)</span>
        )}
        {melding && <span className="hint">{melding}</span>}
      </div>
    </div>
  )
}

const inn = 'ui-input'
const st = (tilstand?: string) =>
  tilstand === 'koeyrer' ? 'var(--color-accent-700)'
    : tilstand === 'feil' ? '#b45309' : 'color-mix(in srgb, var(--color-text) 55%, transparent)'

function Smtp() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchSmtp(), [])
  const { data, refresh } = usePolling<SmtpKonfig>(fetcher, 5000)
  const meldFetcher = useCallback(() => fetchSmtpMeldingar(), [])
  const { data: meld } = usePolling<{ meldingar: SmtpMelding[] }>(meldFetcher, 5000)
  const [u, setU] = useState<Partial<SmtpKonfig> & { passord?: string }>({})
  const [melding, setMelding] = useState<string | null>(null)

  const v = <K extends keyof SmtpKonfig>(f: K): SmtpKonfig[K] =>
    (u[f] !== undefined ? (u[f] as SmtpKonfig[K]) : data?.[f]) as SmtpKonfig[K]
  const sett = (f: string, val: unknown) => setU((o) => ({ ...o, [f]: val }))
  const lagre = async () => {
    setMelding(null)
    try { const r = await lagreSmtp(u); setMelding(r.melding); setU({}); refresh() }
    catch (e) { setMelding(e instanceof Error ? e.message : String(e)) }
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className="card-head" style={{ margin: 0 }}>{t('Email (SMTP) receiver')}</span>
        <span className="tag ml-auto" style={{ color: st(data?.status?.tilstand), border: '1px solid var(--color-divider)' }}>
          {data?.status?.tilstand || '—'}{data?.status?.port ? ` :${data.status.port}` : ''}
        </span>
      </div>
      <p className="hint mb-2">{t('Point the instrument’s SMTP/email settings at this node’s IP. CSV report attachments become channels automatically.')}</p>

      <div className="grid grid-cols-2 gap-2 mb-2">
        <label className="flex items-center gap-2 text-sm col-span-2">
          <input type="checkbox" checked={!!v('aktivert')} onChange={(e) => sett('aktivert', e.target.checked)} />
          {t('Enable SMTP receiver')}
        </label>
        <div><label className="ui-label">{t('Port')}</label>
          <input className={inn} type="number" value={v('port') ?? 25} onChange={(e) => sett('port', Number(e.target.value))} /></div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={!!v('krev_auth')} onChange={(e) => sett('krev_auth', e.target.checked)} />
            {t('Require login')}
          </label>
        </div>
        {v('krev_auth') && <>
          <div><label className="ui-label">{t('Username')}</label>
            <input className={inn} value={v('brukar') ?? ''} onChange={(e) => sett('brukar', e.target.value)} /></div>
          <div><label className="ui-label">{t('Password')}</label>
            <input className={inn} type="password" placeholder={data?.passord_sett ? '••••••••' : ''}
              value={u.passord ?? ''} onChange={(e) => sett('passord', e.target.value)} /></div>
        </>}
        <label className="flex items-center gap-2 text-sm col-span-2">
          <input type="checkbox" checked={v('trekk_kanalar') ?? true} onChange={(e) => sett('trekk_kanalar', e.target.checked)} />
          {t('Turn CSV report attachments into channels')}
        </label>
      </div>
      <div className="flex items-center gap-2">
        <button className="btn-primary" onClick={lagre}>{t('Save')}</button>
        {data?.status?.port && data.status.port < 1024 && (
          <span className="hint">{t('Port <1024 needs the container to run privileged.')}</span>
        )}
        {melding && <span className="hint">{melding}</span>}
      </div>

      {(meld?.meldingar?.length ?? 0) > 0 && (
        <div className="mt-3">
          <span className="meta-label">{t('Recent emails')} ({data?.status?.motteke ?? 0})</span>
          <div className="mt-1 flex flex-col gap-0.5 max-h-40 overflow-y-auto">
            {meld!.meldingar.slice(0, 12).map((m, i) => (
              <div key={i} className="text-[12.5px] flex gap-2">
                <span className="ui-num hint">{new Date(m.ts * 1000).toLocaleTimeString()}</span>
                <span className="truncate flex-1">{m.frå} — {m.emne || t('(no subject)')}</span>
                {m.vedlegg.length > 0 && <span className="hint">📎{m.vedlegg.length}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function Ntp() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchNtp(), [])
  const { data, refresh } = usePolling<NtpKonfig>(fetcher, 5000)
  const [u, setU] = useState<Partial<NtpKonfig>>({})
  const [melding, setMelding] = useState<string | null>(null)
  const v = <K extends keyof NtpKonfig>(f: K): NtpKonfig[K] =>
    (u[f] !== undefined ? (u[f] as NtpKonfig[K]) : data?.[f]) as NtpKonfig[K]
  const sett = (f: string, val: unknown) => setU((o) => ({ ...o, [f]: val }))
  const lagre = async () => {
    setMelding(null)
    try { const r = await lagreNtp(u); setMelding(r.melding); setU({}); refresh() }
    catch (e) { setMelding(e instanceof Error ? e.message : String(e)) }
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className="card-head" style={{ margin: 0 }}>{t('Time (NTP) server')}</span>
        <span className="tag ml-auto" style={{ color: st(data?.status?.tilstand), border: '1px solid var(--color-divider)' }}>
          {data?.status?.tilstand || '—'}{data?.status?.svar ? ` · ${data.status.svar} ${t('replies')}` : ''}
        </span>
      </div>
      <p className="hint mb-2">{t('Point the instrument’s NTP/time settings at this node’s IP so its measurements are correctly timestamped.')}</p>
      <div className="grid grid-cols-2 gap-2 mb-2">
        <label className="flex items-center gap-2 text-sm col-span-2">
          <input type="checkbox" checked={!!v('aktivert')} onChange={(e) => sett('aktivert', e.target.checked)} />
          {t('Enable time server')}
        </label>
        <div><label className="ui-label">{t('Port')}</label>
          <input className={inn} type="number" value={v('port') ?? 123} onChange={(e) => sett('port', Number(e.target.value))} /></div>
        <div><label className="ui-label">{t('Stratum')}</label>
          <input className={inn} type="number" min={1} max={15} value={v('stratum') ?? 3} onChange={(e) => sett('stratum', Number(e.target.value))} /></div>
      </div>
      <div className="flex items-center gap-2">
        <button className="btn-primary" onClick={lagre}>{t('Save')}</button>
        {data?.status?.port && data.status.port < 1024 && (
          <span className="hint">{t('Port <1024 needs the container to run privileged.')}</span>
        )}
        {melding && <span className="hint">{melding}</span>}
      </div>
    </div>
  )
}
