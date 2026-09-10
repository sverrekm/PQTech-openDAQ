import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import {
  fetchFtp, lagreFtp, testFtp, listeFtp, filFtp, synkFtp, kanalarFtp, slettFtp,
} from '../api/instrumentftp'
import type { FtpKonfig, FtpListe, FtpFil, FtpKanalar } from '../api/instrumentftp'
import { useI18n } from '../i18n'
import Panel from './ui/Panel'

/**
 * FTP-henting frå eit instrument som ikkje strøymer.
 *
 * Elspec G4500 gir ikkje måledata over nettet — arkivet ligg på FTP-en
 * hans. Noden hentar nye filer med jamne mellomrom og legg dei under
 * målekatalogen. Kortet lèt brukaren konfigurere det, teste innlogginga,
 * bla i arkivet og sjå kva som er henta.
 */
export default function InstrumentFtpCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchFtp(), [])
  const { data, refresh } = usePolling<FtpKonfig>(fetcher, 5000)

  const [utkast, setUtkast] = useState<Partial<FtpKonfig> & { passord?: string }>({})
  const [feil, setFeil] = useState<string | null>(null)
  const [melding, setMelding] = useState<string | null>(null)
  const [testSvar, setTestSvar] = useState<string | null>(null)
  const [jobbar, setJobbar] = useState<string | null>(null)

  // Bla-i-arkivet
  const [sti, setSti] = useState('/')
  const [liste, setListe] = useState<FtpListe | null>(null)
  const [fil, setFil] = useState<FtpFil | null>(null)

  // Kanalar trekte ut av rapportane
  const kanalFetcher = useCallback(() => kanalarFtp(), [])
  const { data: kanalar } = usePolling<FtpKanalar>(kanalFetcher, 15000)

  // Verdien frå utkastet om brukaren har rørt feltet, elles frå serveren.
  const v = <K extends keyof FtpKonfig>(felt: K): FtpKonfig[K] =>
    (utkast[felt] !== undefined ? (utkast[felt] as FtpKonfig[K]) : data?.[felt]) as FtpKonfig[K]
  const sett = (felt: string, verdi: unknown) =>
    setUtkast((u) => ({ ...u, [felt]: verdi }))

  const lagre = async () => {
    setFeil(null); setMelding(null)
    try {
      const res = await lagreFtp(utkast)
      if (!res.suksess) { setFeil(res.melding); return }
      setMelding(res.melding)
      setUtkast({})
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    }
  }

  const test = async () => {
    setJobbar('test'); setTestSvar(null); setFeil(null)
    try {
      const res = await testFtp(v('vert'))
      setTestSvar(
        (res.velkomst ? `${res.velkomst} — ` : '') +
        (res.melding ?? (res.suksess ? 'OK' : 'Failed')))
    } catch (e) {
      setTestSvar(e instanceof Error ? e.message : String(e))
    } finally {
      setJobbar(null)
    }
  }

  const bla = async (mål: string) => {
    setJobbar('bla'); setFeil(null); setFil(null)
    try {
      const res = await listeFtp(mål, v('vert'))
      setListe(res)
      if (res.suksess) setSti(res.sti ?? mål)
      else setFeil(res.melding ?? 'Listing failed')
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setJobbar(null)
    }
  }

  const titt = async (filsti: string) => {
    setJobbar('fil'); setFil(null)
    try {
      setFil(await filFtp(filsti, v('vert')))
    } catch (e) {
      setFil({ suksess: false, melding: e instanceof Error ? e.message : String(e) })
    } finally {
      setJobbar(null)
    }
  }

  const slett = async (filsti: string, namn: string) => {
    if (!window.confirm(t('Delete this file on the instrument? This cannot be undone.') + `\n\n${namn}`)) return
    setMelding(null); setFeil(null)
    try {
      const r = await slettFtp(filsti, v('vert'))
      if (r.suksess) { setMelding(r.melding); bla(sti) }
      else setFeil(r.melding)
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    }
  }

  const synkNo = async () => {
    setJobbar('synk'); setMelding(null); setFeil(null)
    try {
      const res = await synkFtp()
      if (res.suksess) setMelding(res.melding)
      else setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setJobbar(null)
    }
  }

  const opp = () => {
    const deler = sti.replace(/\/+$/, '').split('/')
    deler.pop()
    bla(deler.join('/') || '/')
  }

  const st = data?.status
  const inn = 'ui-input'
  const knapp = 'btn-primary'
  const knappLys = 'btn-ghost'

  return (
    <Panel
      kicker={t('Retrieval')}
      title={t('Instrument FTP retrieval')}
      sub={t('For instruments that do not stream — the node fetches new files from the instrument’s FTP archive on a schedule.')}
    >
      {feil && (
        <div className="p-2 mb-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{feil}</div>
      )}
      {melding && (
        <div className="p-2 mb-3 bg-green-50 border border-green-200 rounded text-sm text-green-700">{melding}</div>
      )}

      {/* Konfig */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="col-span-2 sm:col-span-1">
          <label className="ui-label">{t('FTP host (IP)')}</label>
          <input className={inn} value={v('vert') ?? ''} placeholder="10.99.0.1"
            onChange={(e) => sett('vert', e.target.value)} />
        </div>
        <div className="col-span-1 sm:col-span-1">
          <label className="ui-label">{t('Port')}</label>
          <input className={inn} type="number" value={v('port') ?? 21}
            onChange={(e) => sett('port', Number(e.target.value))} />
        </div>
        <div>
          <label className="ui-label">{t('Username')}</label>
          <input className={inn} value={v('brukar') ?? ''}
            onChange={(e) => sett('brukar', e.target.value)} />
        </div>
        <div>
          <label className="ui-label">{t('Password')}</label>
          <input className={inn} type="password"
            placeholder={data?.passord_sett ? '••••••••' : ''}
            value={utkast.passord ?? ''}
            onChange={(e) => sett('passord', e.target.value)} />
        </div>
        <div>
          <label className="ui-label">{t('Remote root')}</label>
          <input className={inn} value={v('rot') ?? '/'}
            onChange={(e) => sett('rot', e.target.value)} />
        </div>
        <div>
          <label className="ui-label">{t('File pattern (optional)')}</label>
          <input className={inn} value={v('monster') ?? ''} placeholder="*.csv"
            onChange={(e) => sett('monster', e.target.value)} />
        </div>
        <div>
          <label className="ui-label">{t('Interval (minutes)')}</label>
          <input className={inn} type="number" min={0.5} step={0.5}
            value={v('intervall_min') ?? 10}
            onChange={(e) => sett('intervall_min', Number(e.target.value))} />
        </div>
        <div>
          <label className="ui-label">{t('Channel prefix (optional)')}</label>
          <input className={inn} value={v('kanal_prefiks') ?? ''} placeholder="BB1_"
            onChange={(e) => sett('kanal_prefiks', e.target.value)} />
        </div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" className="rounded"
              checked={!!v('aktivert')}
              onChange={(e) => sett('aktivert', e.target.checked)} />
            {t('Fetch automatically')}
          </label>
        </div>
        <div className="col-span-2 flex items-start">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" className="rounded mt-0.5"
              checked={v('berre_nyaste') ?? true}
              onChange={(e) => sett('berre_nyaste', e.target.checked)} />
            <span>
              {t('Newest report only (recommended)')}
              <span className="block text-xs text-gray-400">
                {t('Fetches just the latest report per type for channels — far gentler on the instrument than downloading the whole archive.')}
              </span>
            </span>
          </label>
        </div>
      </div>

      <div className="flex gap-2 flex-wrap mb-2">
        <button className={knapp} onClick={lagre}>{t('Save')}</button>
        <button className={knappLys} onClick={test} disabled={jobbar === 'test'}>
          {jobbar === 'test' ? t('Testing…') : t('Test connection')}
        </button>
        <button className={knappLys} onClick={() => bla(v('rot') ?? '/')}
          disabled={jobbar === 'bla'}>
          {jobbar === 'bla' ? t('Loading…') : t('Browse archive')}
        </button>
        <button className={knappLys} onClick={synkNo} disabled={jobbar === 'synk'}>
          {jobbar === 'synk' ? t('Syncing…') : t('Fetch now')}
        </button>
      </div>

      {testSvar && (
        <div className="text-xs text-gray-600 mb-3 font-mono break-all">{testSvar}</div>
      )}

      {/* Synk-status */}
      {st && (
        <div className="text-xs text-gray-500 border-t border-gray-100 pt-2 mb-3">
          <span className={
            st.tilstand === 'ok' ? 'text-green-600' :
            st.tilstand === 'feil' ? 'text-red-600' :
            st.tilstand === 'koeyrer' ? 'text-[#D76428]' : 'text-gray-500'
          }>
            {st.tilstand || t('idle')}
          </span>
          {st.melding ? ` — ${st.melding}` : ''}
          {` · ${t('fetched total')}: ${st.totalt_henta}`}
          {` · ${t('known files')}: ${st.henta_kjende}`}
        </div>
      )}

      {/* Kanalar frå rapportane — det som blir pusha til hubben */}
      {kanalar && Object.keys(kanalar.kanalar ?? {}).length > 0 && (
        <div className="border-t border-gray-100 pt-2 mb-3">
          <div className="text-xs font-medium text-gray-600 mb-1">
            {t('Channels from reports (pushed to the hub)')}
          </div>
          {Object.entries(kanalar.detaljar ?? {}).map(([typ, d]) => (
            <div key={typ} className="text-xs text-gray-400 mb-0.5">
              {typ}: {d.fil} — {t('last row')} {d.tid}
            </div>
          ))}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-0.5 mt-1">
            {Object.entries(kanalar.kanalar).map(([namn, verdi]) => (
              <div key={namn} className="text-xs flex justify-between gap-2">
                <span className="text-gray-600 truncate">{namn}</span>
                <span className="font-mono text-gray-800">{verdi}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Arkiv-blar */}
      {liste && (
        <div className="border border-gray-200 rounded mb-2">
          <div className="flex items-center gap-2 px-2 py-1 bg-gray-50 border-b border-gray-200 text-xs">
            <button className="text-[#D76428] hover:underline" onClick={opp}>↑ {t('up')}</button>
            <span className="font-mono text-gray-600">{sti}</span>
          </div>
          <div className="max-h-64 overflow-y-auto">
            {liste.oppforingar.length === 0 ? (
              <div className="px-2 py-2 text-xs text-gray-400">{t('empty')}</div>
            ) : liste.oppforingar.map((o) => (
              <div key={o.namn}
                className="flex items-center gap-2 px-2 py-1 text-xs border-b border-gray-50 hover:bg-gray-50">
                <span className="w-4 text-center">{o.katalog ? '📁' : '📄'}</span>
                {o.katalog ? (
                  <button className="text-[#D76428] hover:underline flex-1 text-left"
                    onClick={() => bla(sti.replace(/\/+$/, '') + '/' + o.namn)}>
                    {o.namn}
                  </button>
                ) : (
                  <button className="flex-1 text-left text-gray-700 hover:underline"
                    onClick={() => titt(sti.replace(/\/+$/, '') + '/' + o.namn)}>
                    {o.namn}
                  </button>
                )}
                <span className="text-gray-400 font-mono">{o.storleik || ''}</span>
                <span className="text-gray-400">{o.dato}</span>
                {!o.katalog && (
                  <button title={t('Delete file')}
                    className="text-gray-400 hover:text-red-600 px-1"
                    onClick={() => slett(sti.replace(/\/+$/, '') + '/' + o.namn, o.namn)}>🗑</button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Fil-titt: kva format er dette eigentleg? */}
      {fil && (
        <div className="border border-gray-200 rounded p-2 text-xs">
          {fil.suksess ? (
            <>
              <div className="text-gray-500 mb-1">
                {fil.sti} · {fil.storleik ?? '?'} B · {t('first bytes')}:
              </div>
              <div className="font-mono break-all text-gray-400 mb-1">{fil.hex}</div>
              <pre className="font-mono whitespace-pre-wrap break-all text-gray-600 max-h-40 overflow-y-auto">
                {fil.tekst}
              </pre>
            </>
          ) : (
            <div className="text-red-600">{fil.melding}</div>
          )}
        </div>
      )}
    </Panel>
  )
}
