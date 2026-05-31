import { useCallback, useState } from 'react'
import type { KanalKonfig, KanalLive, MqttStatus, HubKanal } from '../api/types'
import { fetchHubStatus } from '../api/hub'
import { erKanalSynleg } from '../pages/HubPage'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'

export type View =
  | { page: 'dashboard' }
  | { page: 'settings' }
  | { page: 'channel'; index: number }
  | { page: 'hub' }
  | { page: 'admin' }

interface Props {
  view: View
  onNavigate: (view: View) => void
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
  hubKanalar?: HubKanal[]
}

const COLLAPSE_KEY = 'sidebar_collapsed'

function hentKollapstilstand(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}')
  } catch { return {} }
}

interface SeksjonHeaderProps {
  tittel: string
  antal?: number
  kollapsa: boolean
  onToggle: () => void
}

function SeksjonHeader({ tittel, antal, kollapsa, onToggle }: SeksjonHeaderProps) {
  return (
    <div
      onClick={onToggle}
      className="text-xs font-bold uppercase tracking-wider text-gray-500 hover:text-gray-300 pt-4 pb-1 px-4 cursor-pointer flex items-center justify-between select-none"
    >
      <span>{tittel}{antal !== undefined && antal > 0 ? ` (${antal})` : ''}</span>
      <span className="text-gray-600">{kollapsa ? '▸' : '▾'}</span>
    </div>
  )
}

export default function Sidebar({ view, onNavigate, kanalar, liveData, mqttStatus, hubKanalar }: Props) {
  const { t } = useI18n()
  const [kollapsa, setKollapsa] = useState<Record<string, boolean>>(() => hentKollapstilstand())

  const toggle = (section: string) => {
    setKollapsa(prev => {
      const next = { ...prev, [section]: !prev[section] }
      try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(next)) } catch {}
      return next
    })
  }

  const hasData = (idx: number): boolean => {
    if (!liveData) return false
    const key = `kanal_${idx}`
    const odaq = liveData.opendaq?.[key]
    const drv = liveData.driver?.[key]
    return (odaq?.siste !== undefined) || (drv?.siste !== null && drv?.siste !== undefined)
  }

  const aktiveKanalar = kanalar?.filter(k => k.aktiv) ?? []
  const mqttTopics = mqttStatus?.aktivert && mqttStatus.topics ? Object.entries(mqttStatus.topics) : []
  const synlegeHubKanalar = (hubKanalar ?? []).filter(k => erKanalSynleg(`${k.node_id}:${k.namn}`))

  const activeNavItemClass = "text-white bg-gray-800 border-l-[#D76428]"
  const inactiveNavItemClass = "text-gray-400 hover:bg-gray-800 hover:text-white"
  const baseNavItemClass = "flex items-center gap-2 py-2 px-4 text-sm font-medium cursor-pointer border-l-4 border-transparent transition-colors duration-150 ease-in-out select-none"

  const activeKanalClass = "text-white bg-gray-800 border-l-[#D76428]"
  const inactiveKanalClass = "text-gray-400 hover:bg-gray-800 hover:text-white"
  const baseKanalClass = "flex items-center gap-2 py-1.5 px-4 text-sm cursor-pointer border-l-4 border-transparent transition-colors duration-150 ease-in-out select-none"
  const readOnlyKanalClass = "flex items-center gap-2 py-1.5 px-4 text-sm border-l-4 border-transparent select-none text-gray-400 cursor-default"

  return (
    <nav className="w-56 min-w-56 bg-[#1a1a1a] text-gray-200 flex flex-col border-r border-gray-800 h-full">
      {/* Toppnavigasjon (alltid synleg) */}
      <div className="py-2 flex-shrink-0">
        <div
          className={`${baseNavItemClass} ${view.page === 'dashboard' ? activeNavItemClass : inactiveNavItemClass}`}
          onClick={() => onNavigate({ page: 'dashboard' })}
        >
          {t('Dashboard')}
        </div>
        <div
          className={`${baseNavItemClass} ${view.page === 'hub' ? activeNavItemClass : inactiveNavItemClass}`}
          onClick={() => onNavigate({ page: 'hub' })}
        >
          {t('Hub')}
        </div>
      </div>

      {/* Scrollable midt-seksjon med kollapsible grupper */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {aktiveKanalar.length > 0 && (
          <>
            <SeksjonHeader
              tittel={t('Channels')}
              antal={aktiveKanalar.length}
              kollapsa={!!kollapsa.channels}
              onToggle={() => toggle('channels')}
            />
            {!kollapsa.channels && aktiveKanalar.map(k => (
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

        {mqttTopics.length > 0 && (
          <>
            <SeksjonHeader
              tittel={t('MQTT')}
              antal={mqttTopics.length}
              kollapsa={!!kollapsa.mqtt}
              onToggle={() => toggle('mqtt')}
            />
            {!kollapsa.mqtt && mqttTopics.map(([topic, info]) => (
              <div key={topic} className={readOnlyKanalClass}>
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

        {synlegeHubKanalar.length > 0 && (
          <>
            <SeksjonHeader
              tittel={t('Hub channels')}
              antal={synlegeHubKanalar.length}
              kollapsa={!!kollapsa.hubKanalar}
              onToggle={() => toggle('hubKanalar')}
            />
            {!kollapsa.hubKanalar && synlegeHubKanalar.map(k => (
              <div
                key={`${k.node_id}:${k.namn}`}
                className={readOnlyKanalClass}
              >
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${k.verdi !== null ? 'bg-green-500' : 'bg-gray-500'}`} />
                <span className="truncate">{k.namn}</span>
                {k.verdi !== null && (
                  <span className="ml-auto text-xs font-mono text-gray-400">
                    {k.verdi.toFixed(1)}{k.eining ? ` ${k.eining}` : ''}
                  </span>
                )}
              </div>
            ))}
          </>
        )}

        <HubNodeList
          kollapsa={!!kollapsa.hubNodar}
          onToggle={() => toggle('hubNodar')}
        />
      </div>

      {/* Settings + Admin alltid synleg nederst */}
      <div className="flex-shrink-0 border-t border-gray-800 py-2">
        <div
          className={`${baseNavItemClass} ${view.page === 'settings' ? activeNavItemClass : inactiveNavItemClass}`}
          onClick={() => onNavigate({ page: 'settings' })}
        >
          {t('Settings')}
        </div>
        <div
          className={`${baseNavItemClass} ${view.page === 'admin' ? activeNavItemClass : inactiveNavItemClass}`}
          onClick={() => onNavigate({ page: 'admin' })}
        >
          {t('Admin')}
        </div>
      </div>
    </nav>
  )
}

function HubNodeList({ kollapsa, onToggle }: { kollapsa: boolean, onToggle: () => void }) {
  const { t } = useI18n()
  const hubFetcher = useCallback(() => fetchHubStatus(), [])
  const { data: hub } = usePolling(hubFetcher, 5000)

  const readOnlyKanalClass = "flex items-center gap-2 py-1.5 px-4 text-sm border-l-4 border-transparent select-none text-gray-400 cursor-default"
  const lenkeKanalClass = "group flex items-center gap-2 py-1.5 px-4 text-sm border-l-4 border-transparent select-none text-gray-400 hover:bg-gray-800 hover:text-white cursor-pointer transition-colors duration-150"

  if (!hub?.nodar || hub.nodar.length === 0) return null

  return (
    <>
      <SeksjonHeader
        tittel={t('Hub nodes')}
        antal={hub.nodar.length}
        kollapsa={kollapsa}
        onToggle={onToggle}
      />
      {!kollapsa && hub.nodar.map(node => {
        const prikk = (
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
            node.tilkobla ? 'bg-green-500' : hub.aktiv === false ? 'bg-gray-500' : 'bg-red-500'
          }`} />
        )
        const erOpenDaq = !node.type || node.type === 'opendaq'
        // Berre openDAQ-nodar har web-UI på :8080 — modbus-nodar er reine register.
        if (erOpenDaq) {
          return (
            <a
              key={node.id}
              href={`/node-proxy/${node.id}/`}
              target="_blank"
              rel="noopener noreferrer"
              title={t('Open UI')}
              className={lenkeKanalClass}
            >
              {prikk}
              <span className="truncate">{node.namn}</span>
              <span className="ml-auto flex items-center gap-1 flex-shrink-0">
                {node.tilkobla && (
                  <span className="text-xs font-mono text-gray-500">{node.antal_kanalar}ch</span>
                )}
                <span className="opacity-0 group-hover:opacity-100 transition-opacity text-[#D76428]">↗</span>
              </span>
            </a>
          )
        }
        return (
          <div key={node.id} className={readOnlyKanalClass}>
            {prikk}
            <span className="truncate">{node.namn}</span>
            {node.tilkobla && (
              <span className="ml-auto text-xs font-mono text-gray-500">
                {node.antal_kanalar}ch
              </span>
            )}
          </div>
        )
      })}
    </>
  )
}
