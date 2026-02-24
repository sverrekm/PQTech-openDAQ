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

  const activeNavItemClass = "text-white bg-gray-800 border-l-[#D76428]"
  const inactiveNavItemClass = "text-gray-400 hover:bg-gray-800 hover:text-white"
  const baseNavItemClass = "flex items-center gap-2 py-2 px-4 text-sm font-medium cursor-pointer border-l-4 border-transparent transition-colors duration-150 ease-in-out select-none"

  const activeKanalClass = "text-white bg-gray-800 border-l-[#D76428]"
  const inactiveKanalClass = "text-gray-400 hover:bg-gray-800 hover:text-white"
  const baseKanalClass = "flex items-center gap-2 py-1.5 px-4 text-sm cursor-pointer border-l-4 border-transparent transition-colors duration-150 ease-in-out select-none"


  return (
    <nav className="w-56 min-w-56 bg-[#1a1a1a] text-gray-200 flex flex-col overflow-y-auto border-r border-gray-800">
      <div className="py-2">
        <div
          className={`${baseNavItemClass} ${view.page === 'dashboard' ? activeNavItemClass : inactiveNavItemClass}`}
          onClick={() => onNavigate({ page: 'dashboard' })}
        >
          Oversikt
        </div>
        <div
          className={`${baseNavItemClass} ${view.page === 'settings' ? activeNavItemClass : inactiveNavItemClass}`}
          onClick={() => onNavigate({ page: 'settings' })}
        >
          Innstillingar
        </div>
      </div>

      {aktiveKanalar.length > 0 && (
        <>
          <div className="text-xs font-bold uppercase tracking-wider text-gray-500 pt-4 pb-1 px-4">Kanalar</div>
          {aktiveKanalar.map(k => (
            <div
              key={k.indeks}
              className={`${baseKanalClass} ${view.page === 'channel' && view.index === k.indeks ? activeKanalClass : inactiveKanalClass}`}
              onClick={() => onNavigate({ page: 'channel', index: k.indeks })}
            >
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${hasData(k.indeks) ? 'bg-green-500' : 'bg-gray-500'}`} />
              {k.namn}
            </div>
          ))}
        </>
      )}
    </nav>
  )
}
