import { Fragment, useCallback, useEffect, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchStatus } from '../api/status'
import { fetchSiriusStatus } from '../api/sirius'
import { fetchPushKonfig, oppdaterPushKonfig } from '../api/push'
import type { PushKonfig } from '../api/push'
import { fetchKanalar, fetchKanalLive, oppdaterKanalar } from '../api/kanalar'
import type { KanalKonfig, ServerStatus, SiriusStatus, KanalLive, BufferStatus } from '../api/types'
import { fetchBufferStatus } from '../api/buffer'
import { fetchSisteSkann, startSkann } from '../api/nettskann'
import type { SisteFunn } from '../api/nettskann'
import { apiGet, apiPost } from '../api/client'
import { useI18n } from '../i18n'

/**
 * Field onboarding — den lineære rettleiaren frå Claude Design-mockupen
 * ("Setting up openDAQ in the field").
 *
 * Seks steg som bind saman funksjonar som alt finst: modus (node/hub),
 * namn, instrument-oppdaging, kanalar, datatryggleik og ei verifisering mot
 * LIVE data. Kvar skjerm følgjer blueprint-språket — kvadratiske hårfine
 * rammer, Barlow Condensed-overskrifter, registreringsmerke i hjørna.
 */

const STEG = ['Role', 'Identity', 'Instrument', 'Channels', 'Keeping the data', 'Ready'] as const

interface Props {
  onDone: () => void
}

export default function OnboardingPage({ onDone }: Props) {
  const { t } = useI18n()
  const [steg, setSteg] = useState(0)

  const neste = () => setSteg((s) => Math.min(s + 1, STEG.length - 1))
  const forrige = () => setSteg((s) => Math.max(s - 1, 0))

  // Kontekst-linja øvst: modus + namn, henta ein gong
  const [modus, setModus] = useState<string>('')
  const namnFetcher = useCallback(() => fetchPushKonfig(), [])
  const { data: push } = usePolling<PushKonfig>(namnFetcher, 0)
  useEffect(() => {
    apiGet<{ modus: string; hub_modus: boolean }>('/api/modus')
      .then((m) => setModus(m.hub_modus ? 'hub' : m.modus)).catch(() => {})
  }, [])

  const erNode = modus !== 'hub'
  const kontekst = [
    erNode ? t('Node') : t('Hub'),
    push?.node_namn || undefined,
  ].filter(Boolean).join(' · ')

  return (
    <div className="max-w-4xl mx-auto">
      {/* Kontekst-header (mørk, brand — som resten av appen) */}
      <div className="bg-[#1a1a1a] text-white flex items-center gap-4 px-5 py-3">
        <span className="text-[18px] font-semibold" style={{ fontFamily: 'var(--font-heading)' }}>
          <span className="text-[#D76428]">PQTech</span> openDAQ
        </span>
        <span className="text-[11px] uppercase tracking-[0.14em] text-white/50">
          {t('First-time setup')}
        </span>
        <span className="ml-auto text-xs text-white/70">
          {kontekst || t('No internet — not needed yet')}
        </span>
      </div>

      {/* Steg-framdrift */}
      <div className="flex items-center gap-1.5 px-5 py-2 border-x border-b"
        style={{ borderColor: 'var(--color-divider)' }}>
        {STEG.map((_, i) => (
          <span key={i} className="h-[3px] flex-1"
            style={{ background: i <= steg ? 'var(--color-accent)' : 'var(--color-neutral-300, #d4d4d7)' }} />
        ))}
        <span className="ml-3 text-[11px] uppercase tracking-[0.1em] whitespace-nowrap"
          style={{ color: 'color-mix(in srgb, var(--color-text) 55%, transparent)' }}>
          {t('Step')} {steg + 1} {t('of')} {STEG.length} · {t(STEG[steg])}
        </span>
      </div>

      {/* Steg-innhald i ei blueprint-ramme */}
      <div className="panel border-t-0" style={{ borderRadius: 0 }}>
        <i className="bp-corner bl" /><i className="bp-corner br" />
        {steg === 0 && <StegRole modus={modus} setModus={setModus} onNext={neste} />}
        {steg === 1 && <StegIdentity onNext={neste} onBack={forrige} />}
        {steg === 2 && <StegInstrument onNext={neste} onBack={forrige} erNode={erNode} />}
        {steg === 3 && <StegChannels onNext={neste} onBack={forrige} />}
        {steg === 4 && <StegData onNext={neste} onBack={forrige} />}
        {steg === 5 && <StegReady onDone={onDone} onBack={forrige} ip={push?.node_namn} />}
      </div>
    </div>
  )
}

// --- Delt: tittel + actionrad ---------------------------------------
function Tittel({ kicker, title, sub }: { kicker: string; title: string; sub: string }) {
  return (
    <div className="mb-4">
      <span className="panel-kicker">{kicker}</span>
      <h2 className="panel-title text-[26px]">{title}</h2>
      <p className="panel-sub max-w-[70ch]">{sub}</p>
    </div>
  )
}

function ActionRad({ hint, onBack, onNext, nextLabel, nextDisabled, busy }:
  { hint?: string; onBack?: () => void; onNext?: () => void; nextLabel: string; nextDisabled?: boolean; busy?: boolean }) {
  const { t } = useI18n()
  return (
    <div className="action-row">
      <span className="hint">{hint}</span>
      <div className="flex gap-2">
        {onBack && <button className="btn-ghost" onClick={onBack}>{t('Back')}</button>}
        {onNext && (
          <button className="btn-primary" onClick={onNext} disabled={nextDisabled || busy}>
            {busy ? t('Working…') : nextLabel}
          </button>
        )}
      </div>
    </div>
  )
}

// --- Steg 1: Role ----------------------------------------------------
function StegRole({ modus, setModus, onNext }:
  { modus: string; setModus: (m: string) => void; onNext: () => void }) {
  const { t } = useI18n()
  const [val, setVal] = useState<'node' | 'hub'>(modus === 'hub' ? 'hub' : 'node')
  const [busy, setBusy] = useState(false)
  const [feil, setFeil] = useState<string | null>(null)
  const siriusFetcher = useCallback(() => fetchSiriusStatus(), [])
  const { data: sirius } = usePolling<SiriusStatus>(siriusFetcher, 0)

  const fortsett = async () => {
    const gjeldande = modus === 'hub' ? 'hub' : 'node'
    if (val === gjeldande) { onNext(); return }
    // Modusbyte gjenstartar containeren.
    setBusy(true); setFeil(null)
    try {
      await apiPost<{ suksess: boolean; melding: string }>('/api/modus/bytt',
        { modus: val === 'hub' ? 'hub' : 'direkte' })
      setModus(val)
      // Containeren restartar — vis melding, wizarden lastar på nytt etterpå.
      setFeil(t('Switching mode — the box is restarting. This page will reload.'))
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e)); setBusy(false)
    }
  }

  const Kort = ({ v, tittel, under, punkt }:
    { v: 'node' | 'hub'; tittel: string; under: string; punkt: string }) => (
    <button className="option-card flex-1" data-valgt={val === v} onClick={() => setVal(v)}>
      <div className="text-[17px] font-semibold" style={{ fontFamily: 'var(--font-heading)' }}>{tittel}</div>
      <div className="panel-sub mt-1">{under}</div>
      <div className="text-[12px] mt-2" style={{ color: 'var(--color-accent)' }}>{punkt}</div>
    </button>
  )

  return (
    <div>
      <Tittel kicker={t('Step 1 · Role')} title={t('What is this box doing here?')}
        sub={t('This is the one answer everything else follows from. You can change it later, but it restarts the bridge.')} />
      <div className="flex gap-3 flex-col sm:flex-row">
        <Kort v="node" tittel={t('Node — it measures')}
          under={t('An instrument is wired to this box. It samples, stores locally, and hands its channels onward.')}
          punkt={t('Pick this if you are standing next to the SIRIUS')} />
        <Kort v="hub" tittel={t('Hub — it collects')}
          under={t('No instrument here. It gathers other boxes’ channels and appears to DewesoftX as one device.')}
          punkt={t('Pick this for the box in the office or rack')} />
      </div>
      {sirius?.serienummer && (
        <div className="hint mt-3">
          {t('Serial')} {sirius.serienummer} {t('detected on USB — a node is the usual answer.')}
        </div>
      )}
      {feil && <div className="mt-3 text-sm" style={{ color: 'var(--color-accent-700)' }}>{feil}</div>}
      <ActionRad nextLabel={val === 'hub' ? t('Continue as hub') : t('Continue as node')}
        onNext={fortsett} busy={busy} />
    </div>
  )
}

// --- Steg 2: Identity ------------------------------------------------
function StegIdentity({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { t } = useI18n()
  const [konfig, setKonfig] = useState<PushKonfig | null>(null)
  const [namn, setNamn] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { fetchPushKonfig().then((k) => { setKonfig(k); setNamn(k.node_namn || '') }).catch(() => {}) }, [])

  const fortsett = async () => {
    if (!konfig) { onNext(); return }
    setBusy(true)
    try { await oppdaterPushKonfig({ ...konfig, node_namn: namn.trim() }); onNext() }
    finally { setBusy(false) }
  }

  return (
    <div>
      <Tittel kicker={t('Step 2 · Identity')} title={t('Name this box')}
        sub={t('The name is stamped on every sample this box produces, so a year from now you can still tell where a waveform came from.')} />
      <div className="max-w-md">
        <label className="ui-label">{t('Device name')}</label>
        <input className="ui-input" value={namn} onChange={(e) => setNamn(e.target.value)}
          placeholder={t('e.g. Sundet, Tavle 3')} />
      </div>
      {namn.trim() && (
        <div className="mt-4">
          <span className="meta-label">{t('Where the name shows up')}</span>
          <div className="mt-1 flex flex-wrap gap-2">
            {['Spenning L1', 'Spenning L2', 'Straum L1'].map((c) => (
              <span key={c} className="tag tag-accent ui-num">{namn.trim()}/{c}</span>
            ))}
          </div>
        </div>
      )}
      <ActionRad hint={t('Saved on this box only — nothing leaves it yet.')}
        onBack={onBack} onNext={fortsett} nextLabel={t('Continue')} busy={busy} />
    </div>
  )
}

// --- Steg 3: Instrument ---------------------------------------------
function StegInstrument({ onNext, onBack, erNode }:
  { onNext: () => void; onBack: () => void; erNode: boolean }) {
  const { t } = useI18n()
  const siriusFetcher = useCallback(() => fetchSiriusStatus(), [])
  const { data: sirius } = usePolling<SiriusStatus>(siriusFetcher, 5000)
  const skannFetcher = useCallback(() => fetchSisteSkann(), [])
  const { data: skann } = usePolling<{ subnett: Record<string, SisteFunn> }>(skannFetcher, 5000)
  const [valt, setValt] = useState<string>('')

  // Interessante einingar (Modbus/OPC UA/openDAQ ...) frå siste skann
  const funn = Object.values(skann?.subnett ?? {}).flatMap((s) => s.funn)
    .filter((f) => f.portar?.some((p) => p.interessant))

  const skannNo = async () => {
    // Skann det subnettet noden alt kjenner (om nokon er lagra); elles hopp.
    const subn = Object.keys(skann?.subnett ?? {})[0]
    if (subn) { try { await startSkann(subn) } catch { /* status viser */ } }
  }

  return (
    <div>
      <Tittel kicker={t('Step 3 · Instrument')} title={t('Find the instrument')}
        sub={t('Scanned USB and the local subnet. Pick what you wired up.')} />

      {erNode && (
        <FunnKort valt={valt === 'sirius'} onVel={() => setValt('sirius')}
          tittel={sirius?.serienummer ? `Dewesoft SIRIUS` : t('Dewesoft SIRIUS')}
          detalj={sirius?.tilgjengelig
            ? `USB · ${sirius.serienummer ?? '—'} · ${sirius.data_rate_kbs ? sirius.data_rate_kbs.toFixed(0) + ' KB/s' : t('idle')}`
            : t('not detected on USB')}
          svar={sirius?.streamer ? t('streaming') : sirius?.tilkoblet ? t('connected') : ''} />
      )}

      {funn.map((f) => (
        <FunnKort key={f.ip} valt={valt === f.ip} onVel={() => setValt(f.ip)}
          tittel={f.produsent || f.ip}
          detalj={`${f.ip} · ${f.portar.filter((p) => p.interessant).map((p) => p.namn).join(', ')}`}
          svar={f.sunspec ? t('SunSpec recognised') : ''} />
      ))}

      <button className="btn-ghost mt-2" onClick={skannNo}>{t('Scan again')}</button>

      <details className="mt-3">
        <summary className="hint cursor-pointer">{t('Not the one you expected?')}</summary>
        <ol className="list-decimal ml-5 mt-2 text-[13px] space-y-1"
          style={{ color: 'color-mix(in srgb, var(--color-text) 70%, transparent)' }}>
          <li>{t('Reseat the USB cable at the instrument end — the connector is loose on the HS chassis.')}</li>
          <li>{t('Check the instrument’s own power LED. USB alone will not wake it.')}</li>
          <li>{t('Still nothing? Open Diagnostics — it tests one layer at a time.')}</li>
        </ol>
      </details>

      <ActionRad hint={t('You can add more instruments after setup.')}
        onBack={onBack} onNext={onNext} nextLabel={t('Continue')} />
    </div>
  )
}

function FunnKort({ valt, onVel, tittel, detalj, svar }:
  { valt: boolean; onVel: () => void; tittel: string; detalj: string; svar: string }) {
  const { t } = useI18n()
  return (
    <div className="option-card mb-2 flex items-center gap-3" data-valgt={valt}>
      <div className="min-w-0 flex-1">
        <div className="text-[15px] font-semibold" style={{ fontFamily: 'var(--font-heading)' }}>{tittel}</div>
        <div className="hint ui-num">{detalj}</div>
      </div>
      {svar && <span className="tag tag-accent">{svar}</span>}
      <button className="btn-primary" onClick={onVel}>{valt ? t('Selected') : t('Use this')}</button>
    </div>
  )
}

// --- Steg 4: Channels (full redigering) -----------------------------
// Storleik (quantity) -> standard eining. "Eininga følgjer storleiken —
// du vel aldri begge": vel du Voltage, blir eininga V av seg sjølv.
const KVANTITET: { verdi: string; namn: string; enhet: string; min: number; max: number }[] = [
  { verdi: 'voltage', namn: 'Voltage', enhet: 'V', min: -400, max: 400 },
  { verdi: 'current', namn: 'Current', enhet: 'A', min: 0, max: 100 },
  { verdi: 'acceleration', namn: 'Acceleration', enhet: 'm/s²', min: -50, max: 50 },
  { verdi: 'temperature', namn: 'Temperature', enhet: '°C', min: -40, max: 150 },
  { verdi: 'generic', namn: 'Generic', enhet: '', min: 0, max: 10 },
]
const enhetFor = (type: string) => KVANTITET.find((q) => q.verdi === type)?.enhet ?? ''

// Sensor-bibliotek: to-punkts lineær skalering (0 V/spenn → 0, full spenn → verdi)
const SENSORBIBLIOTEK: { namn: string; enhet: string; inn1: number; ut1: number; inn2: number; ut2: number }[] = [
  { namn: 'Rogowski 6 kA', enhet: 'A', inn1: 0, ut1: 0, inn2: 10, ut2: 6000 },
  { namn: 'Rogowski 3 kA', enhet: 'A', inn1: 0, ut1: 0, inn2: 10, ut2: 3000 },
  { namn: 'Rogowski 1 kA', enhet: 'A', inn1: 0, ut1: 0, inn2: 10, ut2: 1000 },
  { namn: 'CT 100 A / 1 V', enhet: 'A', inn1: 0, ut1: 0, inn2: 1, ut2: 100 },
  { namn: 'Shunt 50 A / 50 mV', enhet: 'A', inn1: 0, ut1: 0, inn2: 0.05, ut2: 50 },
]

// Wiring-patterns: fyller dei første kanalane, slår resten av.
type Rad = { namn: string; type: string }
const MONSTER: Record<string, Rad[]> = {
  '3-phase V + I': [
    { namn: 'Spenning L1', type: 'voltage' }, { namn: 'Spenning L2', type: 'voltage' },
    { namn: 'Spenning L3', type: 'voltage' }, { namn: 'Straum L1', type: 'current' },
    { namn: 'Straum L2', type: 'current' }, { namn: 'Straum L3', type: 'current' },
  ],
  '3-phase voltage': [
    { namn: 'Spenning L1', type: 'voltage' }, { namn: 'Spenning L2', type: 'voltage' },
    { namn: 'Spenning L3', type: 'voltage' },
  ],
  'Single phase': [{ namn: 'Spenning L1', type: 'voltage' }, { namn: 'Straum L1', type: 'current' }],
  'Blank': [],
}

function sensorSlope(k: KanalKonfig): number {
  if (k.sensor_inn_2 === k.sensor_inn_1) return 0
  return (k.sensor_ut_2 - k.sensor_ut_1) / (k.sensor_inn_2 - k.sensor_inn_1)
}

function StegChannels({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { t } = useI18n()
  const [kanalar, setKanalar] = useState<KanalKonfig[]>([])
  const [opna, setOpna] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [melding, setMelding] = useState<{ ok: boolean; text: string } | null>(null)

  const hent = useCallback(async () => {
    try { setKanalar(await fetchKanalar()) } catch { /* tomt */ }
  }, [])
  useEffect(() => { hent() }, [hent])

  const oppdater = (i: number, felt: keyof KanalKonfig, verdi: string | number | boolean) =>
    setKanalar((prev) => prev.map((k, j) => (j === i ? { ...k, [felt]: verdi } : k)))

  // Endrar quantity → set eininga og eit fornuftig område med.
  const settType = (i: number, type: string) => {
    const q = KVANTITET.find((x) => x.verdi === type)
    setKanalar((prev) => prev.map((k, j) => j === i ? {
      ...k, type,
      enhet: k.sensor_aktiv ? k.sensor_enhet : (q?.enhet ?? k.enhet),
      range_min: q?.min ?? k.range_min, range_max: q?.max ?? k.range_max,
    } : k))
  }

  const brukMonster = (namn: string) => {
    const rader = MONSTER[namn] ?? []
    setKanalar((prev) => prev.map((k, i) => {
      const r = rader[i]
      if (!r) return { ...k, aktiv: false }
      const q = KVANTITET.find((x) => x.verdi === r.type)
      return {
        ...k, aktiv: true, namn: r.namn, type: r.type,
        enhet: q?.enhet ?? '', range_min: q?.min ?? k.range_min, range_max: q?.max ?? k.range_max,
        sensor_aktiv: false,
      }
    }))
  }

  const brukSensor = (i: number, s: typeof SENSORBIBLIOTEK[number]) =>
    setKanalar((prev) => prev.map((k, j) => j === i ? {
      ...k, sensor_aktiv: true, sensor_namn: s.namn, sensor_enhet: s.enhet,
      sensor_inn_1: s.inn1, sensor_ut_1: s.ut1, sensor_inn_2: s.inn2, sensor_ut_2: s.ut2,
      enhet: s.enhet, range_max: Math.max(k.range_max, s.ut2),
    } : k))

  const toggleSensor = (i: number) => setOpna((p) => {
    const n = new Set(p); n.has(i) ? n.delete(i) : n.add(i); return n
  })

  const lagre = async () => {
    setBusy(true); setMelding(null)
    try {
      const res = await oppdaterKanalar(kanalar)
      setMelding({ ok: res.suksess, text: res.melding })
      if (res.suksess) onNext()
    } catch (e) {
      setMelding({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally { setBusy(false) }
  }

  const aktive = kanalar.filter((k) => k.aktiv).length

  return (
    <div>
      <Tittel kicker={t('Step 4 · Channels')} title={t('Name what you’re measuring')}
        sub={t('Start from a wiring pattern, then correct the names. The unit follows the quantity — you never pick both.')} />

      {/* Wiring-pattern */}
      <div className="mb-3">
        <span className="ui-label">{t('Wiring')}</span>
        <div className="flex flex-wrap gap-2">
          {Object.keys(MONSTER).map((m) => (
            <button key={m} className="btn-ghost" onClick={() => brukMonster(m)}>{t(m)}</button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="ui-table">
          <thead>
            <tr>
              <th>{t('Slot')}</th><th>{t('Channel name')}</th><th>{t('Quantity')}</th>
              <th>{t('Unit')}</th><th>{t('Range')}</th><th>{t('Recording')}</th>
            </tr>
          </thead>
          <tbody>
            {kanalar.map((k, i) => (
              <Fragment key={k.indeks}>
                <tr style={{ opacity: k.aktiv ? 1 : 0.55 }}>
                  <td className="ui-num">{k.indeks + 1}</td>
                  <td>
                    <input className="ui-input" style={{ minWidth: 130 }} value={k.namn}
                      placeholder={t('unused')} onChange={(e) => oppdater(i, 'namn', e.target.value)} />
                  </td>
                  <td>
                    <select className="ui-select" value={k.type} onChange={(e) => settType(i, e.target.value)}>
                      {KVANTITET.map((q) => <option key={q.verdi} value={q.verdi}>{t(q.namn)}</option>)}
                    </select>
                    {k.type === 'current' && (
                      <button className="btn-ghost mt-1 text-[12px]" onClick={() => toggleSensor(i)}>
                        {k.sensor_aktiv ? `${k.sensor_namn || t('Sensor')} · ${sensorSlope(k).toFixed(0)} ${k.sensor_enhet}/V` : t('via sensor')}
                      </button>
                    )}
                  </td>
                  <td className="ui-num">{k.sensor_aktiv ? k.sensor_enhet : (k.enhet || enhetFor(k.type) || '—')}</td>
                  <td className="ui-num" style={{ whiteSpace: 'nowrap' }}>
                    <input className="ui-input inline-block" style={{ width: 64 }} type="number"
                      value={k.range_min} onChange={(e) => oppdater(i, 'range_min', parseFloat(e.target.value) || 0)} />
                    <span className="mx-1">–</span>
                    <input className="ui-input inline-block" style={{ width: 72 }} type="number"
                      value={k.range_max} onChange={(e) => oppdater(i, 'range_max', parseFloat(e.target.value) || 0)} />
                  </td>
                  <td>
                    <button className="option-card" style={{ padding: '3px 10px', width: 'auto' }}
                      data-valgt={k.aktiv} onClick={() => oppdater(i, 'aktiv', !k.aktiv)}>
                      {k.aktiv ? t('On') : t('Off')}
                    </button>
                  </td>
                </tr>
                {k.type === 'current' && opna.has(i) && (
                  <tr>
                    <td colSpan={6} style={{ background: 'var(--color-accent-100)' }}>
                      <div className="p-2 flex flex-wrap items-end gap-3">
                        <label className="flex items-center gap-2 text-[13px]">
                          <input type="checkbox" checked={k.sensor_aktiv}
                            onChange={(e) => oppdater(i, 'sensor_aktiv', e.target.checked)} />
                          {t('Scale via sensor')}
                        </label>
                        <div>
                          <span className="meta-label">{t('From sensor library')}</span>
                          <select className="ui-select" style={{ minWidth: 160 }} value=""
                            onChange={(e) => { const s = SENSORBIBLIOTEK.find((x) => x.namn === e.target.value); if (s) brukSensor(i, s) }}>
                            <option value="">{t('Choose…')}</option>
                            {SENSORBIBLIOTEK.map((s) => <option key={s.namn} value={s.namn}>{s.namn}</option>)}
                          </select>
                        </div>
                        {k.sensor_aktiv && (
                          <>
                            <div><span className="meta-label">{t('Name')}</span>
                              <input className="ui-input" style={{ width: 130 }} value={k.sensor_namn}
                                onChange={(e) => oppdater(i, 'sensor_namn', e.target.value)} placeholder="Rogowski 6kA" /></div>
                            <div><span className="meta-label">{t('Sensor unit')}</span>
                              <input className="ui-input" style={{ width: 60 }} value={k.sensor_enhet}
                                onChange={(e) => oppdater(i, 'sensor_enhet', e.target.value)} placeholder="A" /></div>
                            <div><span className="meta-label">{t('Point 1')} (V→{k.sensor_enhet || '?'})</span>
                              <div className="flex items-center gap-1">
                                <input className="ui-input" style={{ width: 56 }} type="number" value={k.sensor_inn_1}
                                  onChange={(e) => oppdater(i, 'sensor_inn_1', parseFloat(e.target.value) || 0)} />
                                <span>→</span>
                                <input className="ui-input" style={{ width: 64 }} type="number" value={k.sensor_ut_1}
                                  onChange={(e) => oppdater(i, 'sensor_ut_1', parseFloat(e.target.value) || 0)} />
                              </div></div>
                            <div><span className="meta-label">{t('Point 2')} (V→{k.sensor_enhet || '?'})</span>
                              <div className="flex items-center gap-1">
                                <input className="ui-input" style={{ width: 56 }} type="number" value={k.sensor_inn_2}
                                  onChange={(e) => oppdater(i, 'sensor_inn_2', parseFloat(e.target.value) || 0)} />
                                <span>→</span>
                                <input className="ui-input" style={{ width: 64 }} type="number" value={k.sensor_ut_2}
                                  onChange={(e) => oppdater(i, 'sensor_ut_2', parseFloat(e.target.value) || 0)} />
                              </div></div>
                            <span className="hint">{sensorSlope(k).toFixed(1)} {k.sensor_enhet}/V</span>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {melding && <div className="mt-2 text-sm" style={{ color: melding.ok ? 'var(--color-accent-700)' : '#b45309' }}>{melding.text}</div>}

      <div className="action-row">
        <span className="hint">
          {aktive} {t('of')} {kanalar.length} {t('slots recording')} · {t('unused slots stay off, so they never reach DewesoftX as noise.')}
        </span>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={onBack}>{t('Back')}</button>
          <button className="btn-ghost" onClick={hent}>{t('Revert')}</button>
          <button className="btn-primary" onClick={lagre} disabled={busy}>{busy ? t('Working…') : t('Save & continue')}</button>
        </div>
      </div>
    </div>
  )
}

// --- Steg 5: Keeping the data ---------------------------------------
function StegData({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { t } = useI18n()
  const bufFetcher = useCallback(() => fetchBufferStatus(), [])
  const { data: buf } = usePolling<BufferStatus>(bufFetcher, 5000)
  const pushFetcher = useCallback(() => fetchPushKonfig(), [])
  const { data: push } = usePolling<PushKonfig>(pushFetcher, 0)

  const Lag = ({ tittel, under, status, on }:
    { tittel: string; under: string; status: string; on: boolean }) => (
    <div className="meta-panel mb-2">
      <div className="flex items-center gap-2">
        <span className="text-[15px] font-semibold" style={{ fontFamily: 'var(--font-heading)' }}>{tittel}</span>
        <span className="ml-auto tag" style={{
          background: on ? 'var(--color-accent-100)' : 'transparent',
          color: on ? 'var(--color-accent-800)' : 'color-mix(in srgb, var(--color-text) 50%, transparent)',
          border: on ? 'none' : '1px solid var(--color-divider)',
        }}>{status}</span>
      </div>
      <div className="hint mt-1">{under}</div>
    </div>
  )

  return (
    <div>
      <Tittel kicker={t('Step 5 · Keeping the data')} title={t('Nothing gets lost when the link drops')}
        sub={t('Every sample is written to this box first. Sending it onward is a second, optional job that catches up on its own.')} />

      {buf && (
        <div className="flex flex-wrap gap-4 mb-3 text-[13px]">
          <span><span className="meta-label">{t('Written')}</span><span className="ui-num block">{(buf.totalt_rader ?? 0).toLocaleString()}</span></span>
          <span><span className="meta-label">{t('Rows unsynced')}</span><span className="ui-num block">{(buf.usynkroniserte ?? 0).toLocaleString()}</span></span>
          <span><span className="meta-label">{t('Write rate')}</span><span className="ui-num block">{buf.skriv_per_sek ?? 0}/s</span></span>
          <span><span className="meta-label">{t('Size')}</span><span className="ui-num block">{(buf.storleik_mb ?? 0).toFixed(0)} MB</span></span>
        </div>
      )}

      <Lag tittel={t('On this box')} under={t('Raw samples to the SSD, continuously. This cannot be switched off — it is what makes the box safe to leave alone.')}
        status={buf?.aktivert ? t('Always on') : t('Off')} on={!!buf?.aktivert} />
      <Lag tittel={t('Push to hub')} under={push?.parent_url ? `${push.parent_url} · ${t('catches up whenever the hub is reachable')}` : t('Not set up — set a parent hub in Settings.')}
        status={push?.parent_url ? t('Enabled') : t('Not set up')} on={!!push?.parent_url} />
      <Lag tittel={t('NAS archive')} under={t('Raw-file copies to a mounted share, for the long-term record.')}
        status={t('Optional')} on={false} />

      <ActionRad hint={t('Skipped items become open tasks on the dashboard.')}
        onBack={onBack} onNext={onNext} nextLabel={t('Continue')} />
    </div>
  )
}

// --- Steg 6: Ready to measure ---------------------------------------
function StegReady({ onDone, onBack, ip }: { onDone: () => void; onBack: () => void; ip?: string }) {
  const { t } = useI18n()
  const statusFetcher = useCallback(() => fetchStatus(), [])
  const { data: status } = usePolling<ServerStatus>(statusFetcher, 3000)
  const siriusFetcher = useCallback(() => fetchSiriusStatus(), [])
  const { data: sirius } = usePolling<SiriusStatus>(siriusFetcher, 3000)
  const bufFetcher = useCallback(() => fetchBufferStatus(), [])
  const { data: buf } = usePolling<BufferStatus>(bufFetcher, 3000)
  const liveFetcher = useCallback(() => fetchKanalLive(), [])
  const { data: live } = usePolling<KanalLive>(liveFetcher, 2000)

  const sjekkar: { ok: boolean | null; tekst: string }[] = [
    { ok: sirius?.tilkoblet ?? null, tekst: t('Instrument answering on USB') },
    { ok: sirius?.streamer ?? null, tekst: `${t('ADC streaming')} ${sirius?.data_rate_kbs ? sirius.data_rate_kbs.toFixed(0) + ' KB/s' : ''}` },
    { ok: (status?.kanaler?.length ?? 0) > 0, tekst: t('Channels configured') },
    { ok: buf?.aktivert ?? null, tekst: `${t('Writing to local storage')} ${buf?.skriv_per_sek ? buf.skriv_per_sek + '/s' : ''}` },
    { ok: (status?.servere?.length ?? 0) > 0, tekst: `${t('openDAQ servers up')} ${status?.servere?.length ?? 0}` },
  ]
  const alleOk = sjekkar.every((s) => s.ok)
  const liveVerdiar = Object.entries(live?.opendaq ?? {}).slice(0, 6)

  return (
    <div>
      <Tittel kicker={t('Step 6 · Ready to measure')} title={alleOk ? t('Measuring') : t('Almost there')}
        sub={t('Six checks, all verified against live data rather than saved settings.')} />

      <div className="mb-4">
        {sjekkar.map((s, i) => (
          <div key={i} className="flex items-center gap-2 py-1.5 text-[14px]"
            style={{ borderBottom: '1px solid var(--color-line-soft)' }}>
            <span style={{
              color: s.ok ? 'var(--color-accent)' : s.ok === false ? '#b45309' : 'var(--color-neutral-400, #b7b7ba)',
              fontFamily: 'var(--font-mono)',
            }}>{s.ok ? '✓' : s.ok === false ? '!' : '…'}</span>
            <span>{s.tekst}</span>
          </div>
        ))}
      </div>

      {status?.ip && (
        <div className="meta-panel mb-3">
          <span className="meta-label">{t('Connect DewesoftX')}</span>
          <div className="ui-num text-[15px] mt-0.5">{status.ip}</div>
          <div className="hint mt-1">{t('Or leave it — the box announces itself under Setup › Devices.')}</div>
        </div>
      )}

      {liveVerdiar.length > 0 && (
        <div className="mb-2">
          <span className="meta-label">{t('Live · 2 s window')}</span>
          <div className="mt-1 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1">
            {liveVerdiar.map(([namn, v]) => (
              <div key={namn} className="flex justify-between gap-2 text-[13px]">
                <span className="truncate" style={{ color: 'color-mix(in srgb, var(--color-text) 65%, transparent)' }}>{namn}</span>
                <span className="ui-num">{typeof v.siste === 'number' ? v.siste.toFixed(1) : '—'}</span>
              </div>
            ))}
          </div>
          <div className="hint mt-1">{t('Moving lines mean samples, not settings.')}</div>
        </div>
      )}

      <div className="action-row">
        <span className="hint">{t('You can walk away — the box keeps measuring with the tablet closed.')}</span>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={onBack}>{t('Back')}</button>
          <button className="btn-primary" onClick={onDone}>{t('Done — go to dashboard')}</button>
        </div>
      </div>
      {ip === undefined && null}
    </div>
  )
}
