import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchSisteSkann } from '../api/nettskann'
import type { SisteFunn, SkannFunn } from '../api/nettskann'
import { leggTilPqube, oppdagPqube } from '../api/pqube'
import Panel from './ui/Panel'
import { useI18n } from '../i18n'

/**
 * Instrument funne på nettet — Modbus-målarar (PQube 3 o.l.) frå siste
 * nettskann, klare til å leggjast inn med eitt klikk.
 *
 * PQube 3 er ein målar vi ofte brukar. Skannet kjenner han att (rett
 * register-kart), og «Add as PQube 3» byggjer modbus-noden med heile
 * kartet — så han pollast og strøymer over openDAQ utan manuelt oppsett.
 */
export default function InstrumentFunnCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchSisteSkann(), [])
  const { data } = usePolling<{ subnett: Record<string, SisteFunn> }>(fetcher, 15000)
  const [status, setStatus] = useState<Record<string, string>>({})

  // Alle einingar frå siste skann med Modbus (502) — dedupert på IP.
  const funn: SkannFunn[] = []
  const sett = new Set<string>()
  for (const s of Object.values(data?.subnett ?? {})) {
    for (const f of s.funn) {
      if (sett.has(f.ip)) continue
      if (f.pqube || f.portar?.some((p) => p.port === 502)) { funn.push(f); sett.add(f.ip) }
    }
  }

  const leggTil = async (ip: string, namn: string) => {
    setStatus((m) => ({ ...m, [ip]: '…' }))
    try {
      const r = await leggTilPqube(ip, namn)
      setStatus((m) => ({ ...m, [ip]: r.melding }))
    } catch (e) {
      setStatus((m) => ({ ...m, [ip]: e instanceof Error ? e.message : String(e) }))
    }
  }

  const sjekk = async (ip: string) => {
    setStatus((m) => ({ ...m, [ip]: t('checking…') }))
    try {
      const r = await oppdagPqube(ip)
      setStatus((m) => ({ ...m, [ip]: r.melding || (r.pqube ? 'PQube 3' : t('not a PQube')) }))
    } catch (e) {
      setStatus((m) => ({ ...m, [ip]: e instanceof Error ? e.message : String(e) }))
    }
  }

  return (
    <Panel kicker={t('Discovery')} title={t('Instruments on the network')}
      sub={t('Modbus meters found by the last network scan. PQube 3 is recognised and can be added with its full register map in one click.')}>
      {funn.length === 0 ? (
        <div className="hint">{t('No Modbus meters in the last scan. Run a network scan under Network & nodes.')}</div>
      ) : (
        <div className="flex flex-col gap-2">
          {funn.map((f) => {
            const erPqube = !!f.pqube
            const namn = erPqube ? `PQube 3 ${f.ip}` : (f.produsent || f.ip)
            return (
              <div key={f.ip} className="meta-panel"
                style={erPqube ? { borderColor: 'var(--color-accent)' } : undefined}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="ui-num text-[14px] font-semibold" style={{ fontFamily: 'var(--font-heading)' }}>{f.ip}</span>
                  {erPqube
                    ? <span className="tag tag-accent">PQube 3</span>
                    : <span className="tag" style={{ border: '1px solid var(--color-divider)' }}>Modbus TCP</span>}
                  {f.produsent && <span className="hint">{f.produsent}</span>}
                  <div className="ml-auto flex gap-2">
                    {!erPqube && <button className="btn-ghost" onClick={() => sjekk(f.ip)}>{t('Is it a PQube?')}</button>}
                    <button className="btn-primary" onClick={() => leggTil(f.ip, namn)}>{t('Add as PQube 3')}</button>
                  </div>
                </div>
                {f.pqube?.melding && <div className="hint mt-1">{f.pqube.melding}</div>}
                {status[f.ip] && <div className="mt-1 text-[13px]" style={{ color: 'var(--color-accent-700)' }}>{status[f.ip]}</div>}
              </div>
            )
          })}
        </div>
      )}
      <p className="hint mt-2">{t('Added meters appear under Remote nodes / channels and stream over openDAQ.')}</p>
    </Panel>
  )
}
