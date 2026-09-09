import type { ReactNode } from 'react'

interface Props {
  /** Overskrift (Barlow Condensed) */
  title: ReactNode
  /** Liten versal accent-etikett over tittelen */
  kicker?: ReactNode
  /** Kort forklaring under tittelen */
  sub?: ReactNode
  /** Innhald til høgre for tittelen (status, badge, knapp) */
  right?: ReactNode
  /** Registreringsmerke i hjørna (blueprint-ramme). På som standard. */
  corners?: boolean
  className?: string
  children: ReactNode
}

/**
 * Blueprint-panel — den kvadratiske, hårfine kort-ramma i designspråket.
 *
 * Gjennomsiktig botn, 1px divider-kant, og små registreringsmerke i hjørna
 * (som eit teknisk teikningsark). Overskrift i Barlow Condensed, valfri
 * versal accent-kicker over. Fargane er dagens palett.
 */
export default function Panel({
  title, kicker, sub, right, corners = true, className = '', children,
}: Props) {
  return (
    <div className={`panel mb-4 ${className}`}>
      {corners && (
        <>
          <i className="bp-corner tl" /><i className="bp-corner tr" />
          <i className="bp-corner bl" /><i className="bp-corner br" />
        </>
      )}
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
