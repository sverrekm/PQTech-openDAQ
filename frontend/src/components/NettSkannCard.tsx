import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchSkann, startSkann, stoppSkann, fetchSisteSkann, fetchSkannMaal } from '../api/nettskann'
import type { SkannStatus, SkannFunn, SisteFunn, SkannMaal } from '../api/nettskann'
import { leggTilSunSpec } from '../api/sunspec'
import { leggTilPqube } from '../api/pqube'
import type { SunSpecInfo, LeggTilSvar } from '../api/sunspec'
import { useI18n } from '../i18n'
import Panel from './ui/Panel'

/**
 * Nett-skann — kva står på instrumentnettet.
 *
 * Skannar frå containeren, altså gjennom same rute som målepollinga vil
 * bruke. Svarar eit instrument her, når openDAQ-brua det òg.
 */
export default function NettSkannCard() {
  const { t } = useI18n()
  const [subnett, setSubnett] = useState('')
  const [dev, setDev] = useState('')
  const [feil, setFeil] = useState<string | null>(null)

  const fetcher = useCallback(() => fetchSkann(), [])
  // Tett polling medan skannet går, roleg elles.
  const { data, refresh } = usePolling<SkannStatus>(fetcher, 2000)
  const koeyrer = data?.tilstand === 'koeyrer'

  // Autoskannet held desse oppdaterte i bakgrunnen, so kortet viser kva som
  // staar paa instrumentnettet utan at nokon maa trykkje "skann".
  // Kva som er verdt aa skanne, henta frae noden sjoelv - so ein slepp aa
  // skrive subnett for hand og gjette kva alias som hoeyrer til kva.
  const maalFetcher = useCallback(() => fetchSkannMaal(), [])
  const { data: maal } = usePolling<{ maal: SkannMaal[] }>(maalFetcher, 30000)

  const velMaal = (subn: string) => {
    setSubnett(subn)
    const m = (maal?.maal ?? []).find((x) => x.subnett === subn)
    setDev(m?.grensesnitt ?? '')
  }

  const sisteFetcher = useCallback(() => fetchSisteSkann(), [])
  const { data: lagra } = usePolling<{ subnett: Record<string, SisteFunn> }>(
    sisteFetcher, 20000)

  const start = async () => {
    const s = subnett.trim()
    if (!s) { setFeil(t('Enter a subnet, e.g. 192.168.50.0/24')); return }
    setFeil(null)
    try {
      const res = await startSkann(s, dev || undefined)
      if (!res.suksess) setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    }
  }

  const stopp = async () => {
    try { await stoppSkann(); refresh() } catch { /* status viser resten */ }
  }

  const prosent = data && data.totalt
    ? Math.round((data.ferdig / data.totalt) * 100) : 0

  const rader = (funn: SkannFunn[]) => funn.map((f) => (
    <tr key={f.ip} className="border-b border-gray-100 align-top">
      <td className="py-1.5 pr-3 font-mono text-xs whitespace-nowrap">{f.ip}</td>
      <td className="py-1.5 pr-3">
        {f.portar.length === 0 ? (
          <span className="text-xs text-gray-400">{t('answers ping only')}</span>
        ) : (
          <span className="flex flex-wrap gap-1">
            {f.portar.map((p) => (
              <span
                key={p.port}
                className={
                  'text-xs px-1.5 py-0.5 rounded ' +
                  (p.interessant
                    ? 'bg-[#D76428]/10 text-[#D76428] font-medium'
                    : 'bg-gray-100 text-gray-600')
                }
              >
                {p.port} {p.namn}
              </span>
            ))}
          </span>
        )}
      </td>
      <td className="py-1.5 pr-3">
        {f.portar.some((p) => [80, 8080, 443, 8443].includes(p.port)) && (
          // Relativ lenkje: sida blir servert under /node-proxy/<id>/, so
          // browseren set prefikset sjoelv og proxyen hamnar rett.
          <a
            href={`instrument/${f.ip}${f.portar.some((p) => p.port === 80) ? '' : ':8080'}/`}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-[#D76428] hover:underline whitespace-nowrap"
          >
            {t('Open')} →
          </a>
        )}
      </td>
      <td className="py-1.5 text-xs text-gray-600">
        {f.produsent && (
          <div className="font-medium text-[#D76428]">{f.produsent}</div>
        )}
        {f.server && <div>{f.server}</div>}
        {f.tittel && <div className="text-gray-500">{f.tittel}</div>}
        {f.mac && <div className="text-gray-400 font-mono">{f.mac}</div>}
        {f.sunspec && <SunSpecFunn ip={f.ip} ss={f.sunspec} />}
        {f.pqube && <PqubeFunn ip={f.ip} melding={f.pqube.melding} />}
      </td>
    </tr>
  ))

  const hovud = (
    <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
      <th className="py-1.5 pr-3 font-medium">{t('Address')}</th>
      <th className="py-1.5 pr-3 font-medium">{t('Open ports')}</th>
      <th className="py-1.5 pr-3 font-medium"></th>
      <th className="py-1.5 font-medium">{t('Identification')}</th>
    </tr>
  )

  const lagraRader = Object.entries(lagra?.subnett ?? {})

  return (
    <Panel
      kicker={t('Discovery')}
      title={t('Network scan')}
      sub={t('Scans from the node itself, through the same route the measurement polling uses.')}
    >
      {feil && (
        <div className="p-2 mb-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{feil}</div>
      )}

      <div className="flex gap-2 items-end mb-3 flex-wrap">
        {(maal?.maal ?? []).length > 0 && (
          <div className="flex-1 min-w-[11rem]">
            <label className="ui-label">{t('Network')}</label>
            <select
              value={subnett}
              onChange={(e) => velMaal(e.target.value)}
              disabled={koeyrer}
              className="ui-select"
            >
              <option value="">{t('Choose or type below')}</option>
              {(maal?.maal ?? []).map((m) => (
                <option key={m.subnett} value={m.subnett}>
                  {m.namn} — {m.subnett}{m.grensesnitt ? ` (${m.grensesnitt})` : ''}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="flex-1 min-w-[9rem]">
          <label className="ui-label">{t('Subnet')}</label>
          <input
            type="text"
            value={subnett}
            onChange={(e) => setSubnett(e.target.value)}
            placeholder="192.168.50.0/24"
            disabled={koeyrer}
            className="ui-select"
          />
        </div>
        <div className="flex-1 min-w-[9rem]">
          <label className="ui-label">{t('Interface')}</label>
          <select
            value={dev}
            onChange={(e) => setDev(e.target.value)}
            disabled={koeyrer}
            className="ui-select"
          >
            <option value="">{t('Follow routing')}</option>
            {Array.from(new Set((maal?.maal ?? []).filter((m) => m.kan_binde).map((m) => m.grensesnitt)))
              .map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
        {koeyrer ? (
          <button
            onClick={stopp}
            className="btn-ghost"
          >
            {t('Stop')}
          </button>
        ) : (
          <button
            onClick={start}
            className="btn-primary"
          >
            {t('Scan')}
          </button>
        )}
      </div>

      {koeyrer && (
        <div className="mb-3">
          <div className="h-1.5 bg-gray-100 rounded overflow-hidden">
            <div className="h-full bg-[#D76428] transition-all" style={{ width: `${prosent}%` }} />
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {data?.subnett}{data?.grensesnitt ? ` (${data.grensesnitt})` : ''} — {data?.ferdig}/{data?.totalt} ({prosent}%)
            {data?.melding ? ` · ${data.melding}` : ''}
          </div>
        </div>
      )}

      {!koeyrer && data?.melding && (
        <div className="text-xs text-gray-500 mb-2">
          {data.subnett}: {data.melding}
        </div>
      )}

      {data && data.funn.length > 0 && (
        <div className="overflow-x-auto">
          <table className="ui-table">
            <thead>{hovud}</thead>
            <tbody>{rader(data.funn)}</tbody>
          </table>
        </div>
      )}

      {lagraRader.length > 0 && (
        <div className="mt-4 border-t border-gray-100 pt-3">
          <h3 className="text-xs font-semibold text-gray-600 mb-2">
            {t('Last known on the instrument networks')}
          </h3>
          {lagraRader.map(([subnett, res]) => (
            <div key={subnett} className="mb-3">
              <div className="text-xs text-gray-500 mb-1">
                {subnett} — {res.funn.length} {t('devices')}
                {res.tid ? ` · ${new Date(res.tid * 1000).toLocaleString()}` : ''}
              </div>
              {res.funn.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="ui-table">
                    <thead>{hovud}</thead>
                    <tbody>{rader(res.funn)}</tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}


/**
 * Eit SunSpec-funn, med knappen som gjer det om til kanalar.
 *
 * Produsent og modell kjem frå eininga sjølv, ikkje frå OUI-tabellen: ein
 * invertar som svarar «Ginlong / Solar inverter» har fortalt oss meir enn
 * eit MAC-prefiks nokon gong kan.
 */
function SunSpecFunn({ ip, ss }: { ip: string; ss: SunSpecInfo }) {
  const { t } = useI18n()
  const [jobbar, setJobbar] = useState(false)
  const [svar, setSvar] = useState<LeggTilSvar | null>(null)

  const leggTil = async () => {
    setJobbar(true)
    setSvar(null)
    try {
      setSvar(await leggTilSunSpec(ip, ss.base))
    } catch (e) {
      setSvar({ suksess: false, melding: e instanceof Error ? e.message : String(e) })
    } finally {
      setJobbar(false)
    }
  }

  const namn = [ss.produsent, ss.modell].filter(Boolean).join(' ')

  return (
    <div className="mt-1.5 border-l-2 border-[#D76428] pl-2">
      <div className="font-medium text-[#D76428]">
        SunSpec{namn ? ` — ${namn}` : ''}
      </div>
      {ss.serienr && <div className="text-gray-500">s/n {ss.serienr}</div>}
      {ss.kan_lese ? (
        <button
          onClick={leggTil}
          disabled={jobbar}
          className="mt-1 text-xs px-2 py-0.5 rounded bg-[#D76428] text-white
            hover:bg-[#b8541f] disabled:opacity-50"
        >
          {jobbar ? t('Adding channels…') : t('Add channels')}
        </button>
      ) : (
        <div className="text-gray-400">{t('No models we can read')}</div>
      )}
      {svar && (
        <div className={'mt-1 ' + (svar.suksess ? 'text-green-600' : 'text-red-600')}>
          {svar.melding}
        </div>
      )}
    </div>
  )
}


/** Eit PQube-funn i skannet, med knapp som legg han inn med register-kartet. */
function PqubeFunn({ ip, melding }: { ip: string; melding?: string }) {
  const { t } = useI18n()
  const [jobbar, setJobbar] = useState(false)
  const [svar, setSvar] = useState<string | null>(null)
  const leggTil = async () => {
    setJobbar(true); setSvar(null)
    try { const r = await leggTilPqube(ip, `PQube 3 ${ip}`); setSvar(r.melding) }
    catch (e) { setSvar(e instanceof Error ? e.message : String(e)) }
    finally { setJobbar(false) }
  }
  return (
    <div className="mt-1.5 border-l-2 border-[#D76428] pl-2">
      <div className="font-medium text-[#D76428]">PQube 3</div>
      {melding && <div className="text-gray-500">{melding}</div>}
      <button onClick={leggTil} disabled={jobbar}
        className="mt-1 text-xs px-2 py-0.5 rounded bg-[#D76428] text-white hover:bg-[#b8541f] disabled:opacity-50">
        {jobbar ? t('Adding…') : t('Add as PQube 3')}
      </button>
      {svar && <div className="mt-1 text-green-600">{svar}</div>}
    </div>
  )
}
