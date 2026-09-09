import type { ReactNode } from 'react'

interface Props {
  /** Redaksjonell serif-overskrift */
  title: ReactNode
  /** Liten versal etikett over tittelen (t.d. "TOUR 1 · FIELD ONBOARDING") */
  kicker?: ReactNode
  /** Kort forklaring under tittelen */
  sub?: ReactNode
  /** Innhald til høgre for tittelen (status, badge, knapp) */
  right?: ReactNode
  className?: string
  children: ReactNode
}

/**
 * Instrumentpanel — den flate kort-shellen i designspråket.
 *
 * Hårfin kantlinje i staden for skugge, serif-tittel med valfri versal
 * kicker over. Fargane er dagens palett; sjå `global.css` for klassene.
 */
export default function Panel({ title, kicker, sub, right, className = '', children }: Props) {
  return (
    <div className={`panel mb-4 ${className}`}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          {kicker && <span className="panel-kicker">{kicker}</span>}
          <h2 className="panel-title">{title}</h2>
          {sub && <p className="panel-sub">{sub}</p>}
        </div>
        {right && <div className="flex-shrink-0">{right}</div>}
      </div>
      {children}
    </div>
  )
}
