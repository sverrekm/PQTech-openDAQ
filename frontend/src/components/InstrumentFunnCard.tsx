import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchSisteSkann } from '../api/nettskann'
import type { SisteFunn, SkannFunn } from '../api/nettskann'
import { leggTilPqube, leggTilG4500 } from '../api/pqube'
import Panel from './ui/Panel'
import { useI18n } from '../i18n'

/**
 * Instrument funne på nettet — Modbus-målarar frå siste nettskann, klare til
 * å leggjast inn med eitt klikk.
 *
 * PQube 3 blir kjend att automatisk av skannet. Elspec G4500 (via RS-485→
 * Modbus-TCP-gateway) og andre Modbus-einingar kan leggjast inn ved å velje
 * type + unit-id. Alle byggjer ein modbus_tcp-node med ferdig register-kart
 * som pollast og strøymer over openDAQ.
 */
const TYPAR = [
  { id: 'pqube', namn: 'PQube 3', unit: 1 },
  { id: 'g4500', namn: 'Elspec G4500', unit: 159 },
]

export default function InstrumentFunnCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchSisteSkann(), [])
  const { data } = usePolling<{ subnett: Record<string, SisteFunn> }>(fetcher, 15000)
  const [status, setStatus] = useState<Record<string, string>>({})

  const funn: SkannFunn[] = []
  const sett = new Set<string>()
  for (const s of Object.values(data?.subnett ?? {})) {
    for (const f of s.funn) {
      if (sett.has(f.ip)) continue
      if (f.pqube || f.portar?.some((p) => p.port === 502)) { funn.push(f); sett.add(f.ip) }
    }
  }

  const leggTil = async (ip: string, type: string, unit: number, namn: string) => {
    setStatus((m) => ({ ...m, [ip]: '…' }))
    try {
      const r = type === 'g4500'
        ? await leggTilG4500(ip, unit, namn)
        : await leggTilPqube(ip, namn)
      setStatus((m) => ({ ...m, [ip]: r.melding }))
    } catch (e) {
      setStatus((m) => ({ ...m, [ip]: e instanceof Error ? e.message : String(e) }))
    }
  }

  return (
    <Panel kicker={t('Discovery')} title={t('Instruments on the network')}
      sub={t('Modbus meters found by the last network scan. Pick the type — PQube 3 is auto-recognised; for Elspec G4500 (via an RS-485→Modbus-TCP gateway) choose it and unit 159.')}>
      {funn.length === 0 ? (
        <div className="hint">{t('No Modbus meters in the last scan. Run a network scan under Network & nodes.')}</div>
      ) : (
        <div className="flex flex-col gap-2">
          {funn.map((f) => <Rad key={f.ip} f={f} status={status[f.ip]} onAdd={leggTil} />)}
        </div>
      )}
      <p className="hint mt-2">{t('Added meters appear under Remote nodes / channels and stream over openDAQ.')}</p>
    </Panel>
  )
}

function Rad({ f, status, onAdd }:
  { f: SkannFunn; status?: string; onAdd: (ip: string, type: string, unit: number, namn: string) => void }) {
  const { t } = useI18n()
  const erPqube = !!f.pqube
  const [type, setType] = useState(erPqube ? 'pqube' : 'g4500')
  const valgt = TYPAR.find((x) => x.id === type) ?? TYPAR[0]
  const [unit, setUnit] = useState(valgt.unit)

  const velType = (id: string) => {
    setType(id)
    const ty = TYPAR.find((x) => x.id === id)
    if (ty) setUnit(ty.unit)
  }

  return (
    <div className="meta-panel" style={erPqube ? { borderColor: 'var(--color-accent)' } : undefined}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="ui-num text-[14px] font-semibold" style={{ fontFamily: 'var(--font-heading)' }}>{f.ip}</span>
        {erPqube
          ? <span className="tag tag-accent">PQube 3</span>
          : <span className="tag" style={{ border: '1px solid var(--color-divider)' }}>Modbus TCP</span>}
        {f.produsent && <span className="hint">{f.produsent}</span>}
        <div className="ml-auto flex items-end gap-2">
          <div>
            <span className="meta-label">{t('Type')}</span>
            <select className="ui-select" style={{ minWidth: 130 }} value={type} onChange={(e) => velType(e.target.value)}>
              {TYPAR.map((ty) => <option key={ty.id} value={ty.id}>{ty.namn}</option>)}
            </select>
          </div>
          <div>
            <span className="meta-label">{t('Unit id')}</span>
            <input className="ui-input" style={{ width: 64 }} type="number" value={unit}
              onChange={(e) => setUnit(Number(e.target.value))} />
          </div>
          <button className="btn-primary" onClick={() => onAdd(f.ip, type, unit, `${valgt.namn} ${f.ip}`)}>
            {t('Add')}
          </button>
        </div>
      </div>
      {f.pqube?.melding && <div className="hint mt-1">{f.pqube.melding}</div>}
      {status && <div className="mt-1 text-[13px]" style={{ color: 'var(--color-accent-700)' }}>{status}</div>}
    </div>
  )
}
