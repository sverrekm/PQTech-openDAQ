import type { ReactNode } from 'react'
import SparklineChart from './SparklineChart'

export interface Meter {
  key: string
  num: string
  namn: string
  verdi: string
  eining: string
  kjelde: string
  farge: string
  spark: number[]
  onClick?: () => void
}

interface Props {
  kicker: string
  tittel: string
  meta?: ReactNode
  meters: Meter[]
  tomtekst: string
}

/**
 * Presentasjons-hero i blueprint-språket: nummerert mono-kicker + overskrift,
 * så eit auto-fit rutenett av store mono-avlesingar (verdi + eining + spark,
 * kjelde-tagg). Delt av node-dashbordet (InstrumentMeterGrid) og hub-dashbordet
 * (HubMeterGrid) så dei ser like ut.
 */
export default function MeterGrid({ kicker, tittel, meta, meters, tomtekst }: Props) {
  return (
    <section>
      <div className="flex items-end justify-between gap-4 mb-3">
        <div>
          <span className="mono block text-[9px] tracking-[0.16em] uppercase" style={{ color: 'var(--color-accent-700)' }}>
            {kicker}
          </span>
          <h2 className="text-[27px] leading-none mt-0.5" style={{ fontFamily: 'var(--font-heading)', fontWeight: 600 }}>
            {tittel}
          </h2>
        </div>
        {meta && (
          <div className="mono flex gap-5 text-[11px] tracking-[0.06em] uppercase pb-1" style={{ color: 'rgba(26,26,26,0.55)' }}>
            {meta}
          </div>
        )}
      </div>

      {meters.length === 0 ? (
        <div className="blueprint p-6 text-center text-sm" style={{ color: 'rgba(26,26,26,0.55)' }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {tomtekst}
        </div>
      ) : (
        <div className="blueprint" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(230px,1fr))' }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {meters.map(m => (
            <div
              key={m.key}
              onClick={m.onClick}
              className={m.onClick ? 'cursor-pointer transition-colors hover:bg-black/[0.02]' : ''}
              style={{
                padding: '14px 16px 12px',
                borderRight: '1px solid var(--color-divider)',
                borderBottom: '1px solid var(--color-divider)',
                minWidth: 0,
              }}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="mono text-[10px] tracking-[0.12em]" style={{ color: 'rgba(26,26,26,0.45)' }}>{m.num}</span>
                <span className="mono text-[9px] tracking-[0.12em] uppercase truncate" style={{ color: m.farge, maxWidth: '55%' }}>{m.kjelde}</span>
              </div>
              <div
                className="mt-1.5 overflow-hidden text-ellipsis whitespace-nowrap"
                style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 15, letterSpacing: '0.01em', textTransform: 'uppercase', color: 'rgba(26,26,26,0.72)' }}
              >
                {m.namn}
              </div>
              <div className="flex items-end justify-between gap-2.5 mt-0.5">
                <div className="flex items-baseline gap-1.5 min-w-0 overflow-hidden">
                  <span className="mono" style={{ fontSize: 30, lineHeight: 1.05, fontWeight: 500, color: m.farge }}>{m.verdi}</span>
                  <span className="mono text-[12px]" style={{ color: 'rgba(26,26,26,0.5)' }}>{m.eining}</span>
                </div>
                <div style={{ width: 72, flexShrink: 0 }}>
                  <SparklineChart data={[...m.spark]} />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
