import { useCallback } from 'react'
import type { KanalKonfig, KanalLive, MqttStatus } from '../api/types'
import { fetchHubStatus } from '../api/hub'
import { usePolling } from '../hooks/usePolling'

export type View =
  | { page: 'dashboard' }
  | { page: 'settings' }
  | { page: 'channel'; index: number }
  | { page: 'hub' }

interface Props {
  view: View
  onNavigate: (view: View) => void
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
}

export default function Sidebar({ view, onNavigate, kanalar, liveData, mqttStatus }: Props) {
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
          className={`${baseNavItemClass} ${view.page === 'hub' ? activeNavItemClass : inactiveNavItemClass}`}
          onClick={() => onNavigate({ page: 'hub' })}
        >
          Hub
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

      {mqttStatus?.aktivert && mqttStatus.topics && Object.keys(mqttStatus.topics).length > 0 && (
        <>
          <div className="text-xs font-bold uppercase tracking-wider text-gray-500 pt-4 pb-1 px-4">MQTT</div>
          {Object.entries(mqttStatus.topics).map(([topic, info]) => (
            <div
              key={topic}
              className={`${baseKanalClass} ${inactiveKanalClass} cursor-default`}
            >
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${info.verdi !== null ? 'bg-green-500' : 'bg-yellow-500'}`} />
              <span className="truncate">{info.namn || topic}</span>
              {info.verdi !== null && (
                <span className="ml-auto text-xs font-mono text-gray-400">
                  {info.verdi.toFixed(1)}{info.enhet ? ` ${info.enhet}` : ''}
                </span>
              )}
            </div>
          ))}
        </>
      )}

      {/* Hub-nodar i sidepanelet (viss det finst konfigurerte nodar) */}
      <HubNodeList />
    </nav>
  )
}

function HubNodeList() {
  const hubFetcher = useCallback(() => fetchHubStatus(), [])
  const { data: hub } = usePolling(hubFetcher, 5000)

  const baseKanalClass = "flex items-center gap-2 py-1.5 px-4 text-sm border-l-4 border-transparent select-none"
  const inactiveKanalClass = "text-gray-400 cursor-default"

  if (!hub?.nodar || hub.nodar.length === 0) return null

  return (
    <>
      <div className="text-xs font-bold uppercase tracking-wider text-gray-500 pt-4 pb-1 px-4">Hub-nodar</div>
      {hub.nodar.map(node => (
        <div
          key={node.id}
          className={`${baseKanalClass} ${inactiveKanalClass}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
            node.tilkobla ? 'bg-green-500' : hub.aktiv === false ? 'bg-gray-500' : 'bg-red-500'
          }`} />
          <span className="truncate">{node.namn}</span>
          {node.tilkobla && (
            <span className="ml-auto text-xs font-mono text-gray-500">
              {node.antal_kanalar}ch
            </span>
          )}
        </div>
      ))}
    </>
  )
}
