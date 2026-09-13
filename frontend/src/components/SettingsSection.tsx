import { useState, useEffect, type ReactNode } from 'react'

interface Props {
  id: string
  num: string
  tittel: string
  sub?: string
  defaultOpen?: boolean
  children: ReactNode
}

/** Hendings-namn: index-klikk ber ei seksjon opne seg og scrolle i syne. */
export const OPNE_SEKSJON = 'settings-open-section'

/**
 * Ei kategori på Settings-sida, i blueprint-språket: nummerert mono-kicker
 * (NN — Tittel), overskrift, kort skildring, deretter korta. Kollapsibel;
 * opne/lukka lagra per seksjon i localStorage. Den sticky index-lista til
 * venstre kan opne og scrolle hit via OPNE_SEKSJON-hendinga.
 */
export default function SettingsSection({ id, num, tittel, sub, defaultOpen = false, children }: Props) {
  const key = `settings_open_${id}`
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(key)
      return v === null ? defaultOpen : v === '1'
    } catch {
      return defaultOpen
    }
  })

  const sett = (n: boolean) => {
    setOpen(n)
    try { localStorage.setItem(key, n ? '1' : '0') } catch { /* ignore */ }
  }
  const toggle = () => sett(!open)

  useEffect(() => {
    const h = (e: Event) => {
      if ((e as CustomEvent).detail === id) {
        sett(true)
        // Vent til innhaldet er utvida før vi scrollar.
        requestAnimationFrame(() => {
          document.getElementById(`set-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
        })
      }
    }
    window.addEventListener(OPNE_SEKSJON, h)
    return () => window.removeEventListener(OPNE_SEKSJON, h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  return (
    <section id={`set-${id}`} className="blueprint mb-6" style={{ padding: 20, scrollMarginTop: 24 }}>
      <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
      <button onClick={toggle} className="w-full text-left select-none group">
        <span className="mono block text-[9px] tracking-[0.16em] uppercase" style={{ color: 'var(--color-accent-700)' }}>
          {num} — {tittel}
        </span>
        <span className="flex items-center justify-between gap-2 mt-1">
          <span className="text-[23px] leading-tight" style={{ fontFamily: 'var(--font-heading)', fontWeight: 600 }}>{tittel}</span>
          <span className="text-gray-400 text-lg leading-none flex-shrink-0">{open ? '▾' : '▸'}</span>
        </span>
        {sub && <span className="block text-[13px] mt-1" style={{ color: 'rgba(26,26,26,0.6)', maxWidth: '62ch' }}>{sub}</span>}
      </button>
      {open && <div className="mt-4">{children}</div>}
    </section>
  )
}
