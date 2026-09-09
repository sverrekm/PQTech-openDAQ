import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchInstrumentNett, testInstrument } from '../api/instrumentnett'
import type { InstrumentNettStatus } from '../api/instrumentnett'
import { fetchNat, lagreNat } from '../api/instrumentnat'
import type { NatStatus, NatNett } from '../api/instrumentnat'
import { fetchByggOm, byggOm } from '../api/vert'
import type { ByggOmStatus } from '../api/vert'
import { fetchSisteSkann } from '../api/nettskann'
import type { SisteFunn } from '../api/nettskann'
import GrensesnittVeljar from './GrensesnittVeljar'
import { useI18n } from '../i18n'

/**
 * Instrumentnett — eitt kort for heile jobben.
 *
 * Dette var tre kort før: bridge-status, NAT-oppsett og ruter. For ein ny
 * brukar er det éin oppgåve — «gi noden tilgang til instrumenta» — så det
 * er sett opp som tre nummererte steg med skjemaet skjult til det trengst.
 */

const felt =
  'block w-full rounded border border-gray-300 px-2 py-1.5 text-sm ' +
  'focus:ring-2 focus:ring-[#D76428] outline-none'
const merke = 'block text-xs font-medium text-gray-600 mb-1'

function Steg({ nr, tittel, hint, children }: {
  nr: number; tittel: string; hint?: string; children: React.ReactNode
}) {
  return (
    <section className="mb-5 last:mb-0">
      <div className="flex items-baseline gap-2 mb-1">
        <span className="flex-none w-5 h-5 rounded-full bg-gray-100 text-gray-600
                         text-[11px] font-semibold grid place-items-center">
          {nr}
        </span>
        <h3 className="text-sm font-semibold text-gray-800">{tittel}</h3>
      </div>
      {hint && <p className="text-xs text-gray-500 mb-2 ml-7">{hint}</p>}
      <div className="ml-7">{children}</div>
    </section>
  )
}

export default function InstrumentNettverkCard() {
  const { t } = useI18n()
  const [opent, setOpent] = useState(false)
  const [namn, setNamn] = useState('')
  const [dev, setDev] = useState('wlan0')
  const [ekte, setEkte] = useState('192.168.1.0/24')
  const [alias, setAlias] = useState('10.99.0.0/24')
  const [oversett, setOversett] = useState(true)
  const [testAdr, setTestAdr] = useState('')
  const [laddar, setLaddar] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [feil, setFeil] = useState<string | null>(null)

  const ruterF = useCallback(() => fetchInstrumentNett(), [])
  const natF = useCallback(() => fetchNat(), [])
  const byggF = useCallback(() => fetchByggOm(), [])
  const skannF = useCallback(() => fetchSisteSkann(), [])
  const { data: ruter, refresh: ruterRefresh } =
    usePolling<InstrumentNettStatus>(ruterF, 10000)
  const { data: nat, refresh: natRefresh } = usePolling<NatStatus>(natF, 10000)
  const { data: bygg, refresh: byggRefresh } = usePolling<ByggOmStatus>(byggF, 15000)
  const { data: skann } = usePolling<{ subnett: Record<string, SisteFunn> }>(
    skannF, 20000)

  const klar = ruter?.bru_tilgjengeleg ?? false

  const kjoerByggOm = async () => {
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await byggOm()
      if (res.suksess) setMelding(res.melding); else setFeil(res.melding)
      byggRefresh()
    } catch {
      setMelding(t('Rebuilding — the node is down for about half a minute. Reload shortly.'))
    } finally { setLaddar(false) }
  }

  const lagreNett = async (nett: NatNett[]) => {
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await lagreNat({ aktivert: nat?.aktivert ?? true, nett })
      if (res.suksess) setMelding(res.melding); else setFeil(res.melding)
      natRefresh(); ruterRefresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally { setLaddar(false) }
  }

  const leggTil = async () => {
    if (!ekte.trim()) { setFeil(t('The instrument subnet is required')); return }
    const i = nat?.nett.length ?? 0
    await lagreNett([...(nat?.nett ?? []), {
      namn: namn.trim(), grensesnitt: dev,
      ekte: ekte.trim(),
      alias: oversett ? alias.trim() : ekte.trim(),
      tabell: 99 + i, merke: 99 + i,
    }])
    setNamn(''); setOpent(false)
  }

  const test = async () => {
    const a = testAdr.trim()
    if (!a) { setFeil(t('Enter an instrument address')); return }
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await testInstrument({ host: a })
      if (res.ok) setMelding(res.melding); else setFeil(res.melding)
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally { setLaddar(false) }
  }

  const funne = Object.entries(skann?.subnett ?? {})

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h2 className="text-base font-semibold text-gray-800 mb-1">
        {t('Instrument networks')}
      </h2>
      <p className="text-xs text-gray-500 mb-4">
        {t('Give the node access to instruments on a separate network — an instrument Wi-Fi, a spare ethernet port, or an isolated segment.')}
      </p>

      {feil && <div className="p-2 mb-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{feil}</div>}
      {melding && <div className="p-2 mb-3 bg-green-50 border border-green-200 rounded text-sm text-green-700">{melding}</div>}

      <Steg nr={1} tittel={t('Container network')}
            hint={t('The container needs its own way out to reach anything beyond the LAN.')}>
        {klar ? (
          <div className="text-sm text-green-700">
            {t('Ready')}
            {ruter?.bru && 'dev' in ruter.bru && (
              <span className="text-xs text-gray-500 ml-2">
                {(ruter.bru as { dev: string; gateway: string }).dev} → {(ruter.bru as { dev: string; gateway: string }).gateway}
              </span>
            )}
          </div>
        ) : (
          <div>
            <div className="text-sm text-amber-800 mb-2">
              {t('Not set up yet — the container has to be rebuilt once.')}
            </div>
            {bygg && (bygg.docker && bygg.trygt ? (
              <>
                <button onClick={kjoerByggOm} disabled={laddar}
                  className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50">
                  {t('Rebuild the container now')}
                </button>
                <div className="text-xs text-gray-500 mt-1">
                  {bygg.repo} · {bygg.grensesnitt}
                  {Object.keys(bygg.manglar ?? {}).length > 0
                    ? ` · ${t('will set')} ${Object.entries(bygg.manglar).map(([k, v]) => `${k}=${v}`).join(', ')}`
                    : ''}
                </div>
              </>
            ) : (
              <div className="text-xs text-gray-500">{bygg.grunn}</div>
            ))}
            {bygg?.siste_utdata && (
              <details className="mt-2">
                <summary className="text-xs text-gray-500 cursor-pointer">
                  {t('Last rebuild output')}
                </summary>
                <pre className="mt-1 text-[11px] bg-gray-50 border border-gray-200 rounded p-2 overflow-x-auto whitespace-pre-wrap">{bygg.siste_utdata}</pre>
              </details>
            )}
          </div>
        )}
      </Steg>

      <Steg nr={2} tittel={t('Networks')}
            hint={t('Each instrument network the node should be able to reach.')}>
        {nat && nat.nett.length > 0 ? (
          <div className="mb-2 space-y-1">
            {nat.nett.map((n) => (
              <div key={n.alias}
                   className="flex items-center gap-2 text-sm border border-gray-200 rounded px-2 py-1.5">
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate">{n.namn || n.ekte}</div>
                  <div className="text-xs text-gray-500 font-mono">
                    {n.alias === n.ekte
                      ? n.ekte
                      : `${n.alias} → ${n.ekte}`} · {n.grensesnitt}
                  </div>
                </div>
                <span className={'text-xs flex-none ' + (n.aktiv ? 'text-green-700' : 'text-gray-400')}>
                  {n.aktiv ? t('active') : t('inactive')}
                </span>
                <button
                  onClick={() => lagreNett((nat?.nett ?? []).filter((x) => x.alias !== n.alias))}
                  disabled={laddar}
                  className="text-xs text-gray-400 hover:text-red-600 flex-none disabled:opacity-50">
                  {t('Remove')}
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400 mb-2">{t('None yet.')}</p>
        )}

        {!opent ? (
          <button onClick={() => setOpent(true)}
            className="text-sm text-[#D76428] hover:underline">
            + {t('Add a network')}
          </button>
        ) : (
          <div className="border border-gray-200 rounded p-3 space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className={merke}>{t('Name')}</label>
                <input className={felt} value={namn} onChange={(e) => setNamn(e.target.value)}
                  placeholder={t('e.g. Elspec BlackBox')} />
              </div>
              <div>
                <label className={merke}>{t('Where is it')}</label>
                <GrensesnittVeljar verdi={dev} onEndra={setDev} className={felt} />
              </div>
            </div>

            <div>
              <label className={merke}>{t("The instrument's own subnet")}</label>
              <input className={felt} value={ekte} onChange={(e) => setEkte(e.target.value)}
                placeholder="192.168.1.0/24" />
            </div>

            <fieldset className="space-y-1.5">
              <legend className={merke}>{t('How should the node reach it?')}</legend>
              <label className="flex items-start gap-2 text-sm">
                <input type="radio" className="mt-1" checked={oversett}
                  onChange={() => setOversett(true)} />
                <span>
                  {t('Translate the addresses')}
                  <span className="block text-xs text-gray-500">
                    {t('Use when the instrument clashes with your LAN — most instruments ship on 192.168.1.x. The two networks stay completely separate.')}
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 text-sm">
                <input type="radio" className="mt-1" checked={!oversett}
                  onChange={() => setOversett(false)} />
                <span>
                  {t('Route it directly')}
                  <span className="block text-xs text-gray-500">
                    {t('Use when the instrument is already on a subnet of its own.')}
                  </span>
                </span>
              </label>
            </fieldset>

            {oversett && (
              <div>
                <label className={merke}>{t('Reach it at')}</label>
                <input className={felt} value={alias} onChange={(e) => setAlias(e.target.value)}
                  placeholder="10.99.0.0/24" />
                <p className="text-xs text-gray-500 mt-1">
                  {t('A free range, same size as above. The last part is kept, so')}{' '}
                  <code className="bg-gray-100 px-1 rounded">
                    {(alias.split('/')[0] || '10.99.0.0').replace(/\.0$/, '.1')}
                  </code>{' '}
                  {t('is')}{' '}
                  <code className="bg-gray-100 px-1 rounded">
                    {(ekte.split('/')[0] || '192.168.1.0').replace(/\.0$/, '.1')}
                  </code>.
                </p>
              </div>
            )}

            <div className="flex gap-2">
              <button onClick={leggTil} disabled={laddar}
                className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50">
                {t('Add')}
              </button>
              <button onClick={() => setOpent(false)}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">
                {t('Cancel')}
              </button>
            </div>
          </div>
        )}
      </Steg>

      <Steg nr={3} tittel={t('Devices found')}
            hint={t('Scanned automatically. Highlighted ports are the ones you can read measurements from.')}>
        {funne.length === 0 ? (
          <p className="text-sm text-gray-400">{t('Nothing found yet.')}</p>
        ) : (
          funne.map(([subnett, res]) => (
            <div key={subnett} className="mb-3 last:mb-0">
              <div className="text-xs text-gray-500 mb-1">
                {subnett} · {res.funn.length} {t('devices')}
                {res.tid ? ` · ${new Date(res.tid * 1000).toLocaleTimeString()}` : ''}
              </div>
              <div className="space-y-1">
                {res.funn.map((f) => {
                  // Ein webserver tyder at eininga har eit GUI vi kan
                  // vidareformidle. Relativ lenkje: sida blir servert under
                  // /node-proxy/<id>/, so browseren set prefikset sjoelv.
                  const web = f.portar.find(
                    (p) => [80, 8080, 443, 8443].includes(p.port))
                  return (
                    <div key={f.ip}
                         className="text-sm flex flex-wrap items-baseline gap-x-2">
                      <span className="font-mono text-xs w-28 flex-none">{f.ip}</span>
                      {f.portar.filter((p) => p.interessant).map((p) => (
                        <span key={p.port}
                          className="text-xs px-1.5 py-0.5 rounded bg-[#D76428]/10 text-[#D76428] font-medium">
                          {p.port} {p.namn}
                        </span>
                      ))}
                      {(f.produsent || f.server || f.tittel) && (
                        <span className="text-xs text-gray-500">
                          {f.produsent && (
                            <span className="text-[#D76428] font-medium">{f.produsent} </span>
                          )}
                          {f.server}
                          {f.tittel ? ` ${f.tittel}` : ''}
                        </span>
                      )}
                      {web && (
                        <a
                          href={`instrument/${f.ip}${web.port === 80 ? '' : ':' + web.port}/`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-[#D76428] hover:underline whitespace-nowrap ml-auto"
                        >
                          {t('Open web UI')} &rarr;
                        </a>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ))
        )}

        <div className="flex gap-2 items-end mt-3 pt-3 border-t border-gray-100">
          <div className="flex-1">
            <label className={merke}>{t('Check one address')}</label>
            <input className={felt} value={testAdr} onChange={(e) => setTestAdr(e.target.value)}
              placeholder="10.99.0.1" />
          </div>
          <button onClick={test} disabled={laddar}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">
            {t('Test')}
          </button>
        </div>
      </Steg>
    </div>
  )
}
