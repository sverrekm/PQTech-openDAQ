import type { KanalKonfig, KanalLive } from '../api/types'

export type View =
  | { page: 'dashboard' }
  | { page: 'settings' }
  | { page: 'channel'; index: number }

interface Props {
  view: View
  onNavigate: (view: View) => void
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
}

export default function Sidebar({ view, onNavigate, kanalar, liveData }: Props) {
  const hasData = (idx: number): boolean => {
    if (!liveData) return false
    const key = `kanal_${idx}`
    const odaq = liveData.opendaq?.[key]
    const drv = liveData.driver?.[key]
    return (odaq?.siste !== undefined) || (drv?.siste !== null && drv?.siste !== undefined)
  }

  const aktiveKanalar = kanalar?.filter(k => k.aktiv) ?? []

  return (
    <nav className="sidebar">
      <div className="sidebar-nav">
        <div
          className={`sidebar-nav-item ${view.page === 'dashboard' ? 'active' : ''}`}
          onClick={() => onNavigate({ page: 'dashboard' })}
        >
          Dashboard
        </div>
        <div
          className={`sidebar-nav-item ${view.page === 'settings' ? 'active' : ''}`}
          onClick={() => onNavigate({ page: 'settings' })}
        >
          Settings
        </div>
      </div>

      {aktiveKanalar.length > 0 && (
        <>
          <div className="sidebar-seksjon">Kanalar</div>
          {aktiveKanalar.map(k => (
            <div
              key={k.indeks}
              className={`sidebar-kanal ${view.page === 'channel' && view.index === k.indeks ? 'active' : ''}`}
              onClick={() => onNavigate({ page: 'channel', index: k.indeks })}
            >
              <span className={`kanal-dot ${hasData(k.indeks) ? 'dot-data' : 'dot-inaktiv'}`} />
              {k.namn}
            </div>
          ))}
        </>
      )}
    </nav>
  )
}
