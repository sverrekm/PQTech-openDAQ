import { useState, type ReactNode } from 'react'

interface Props {
  id: string
  tittel: string
  defaultOpen?: boolean
  children: ReactNode
}

/** Kollapsibel undergruppe på Settings-sida. Opne/lukka-tilstand vert lagra
 *  per seksjon i localStorage. */
export default function SettingsSection({ id, tittel, defaultOpen = false, children }: Props) {
  const key = `settings_open_${id}`
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(key)
      return v === null ? defaultOpen : v === '1'
    } catch {
      return defaultOpen
    }
  })

  const toggle = () => setOpen(o => {
    const n = !o
    try { localStorage.setItem(key, n ? '1' : '0') } catch { /* ignore */ }
    return n
  })

  return (
    <div className="mb-5">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between text-left mb-2 px-1 py-1.5 border-b-2 border-gray-200 group select-none"
      >
        <span className="text-sm font-bold uppercase tracking-wider text-gray-600 group-hover:text-gray-900">{tittel}</span>
        <span className="text-gray-400 text-lg leading-none">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div>{children}</div>}
    </div>
  )
}
