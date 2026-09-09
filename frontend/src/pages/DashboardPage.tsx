import type { ReactNode } from 'react'
import type { ServerStatus, KanalKonfig, KanalLive, MqttStatus, HubKanal } from '../api/types'
import SiriusStatusCard from '../components/SiriusStatusCard'
import UsbIpCard from '../components/UsbIpCard'
import ChannelLiveCard from '../components/ChannelLiveCard'
import OpenDaqBridgeCard from '../components/OpenDaqBridgeCard'
import ServerStatusCard from '../components/ServerStatusCard'
import LogViewer from '../components/LogViewer'
import { RemoteBufferStatusCard } from '../components/BufferStatusCard'
import EventListCard from '../components/EventListCard'
import MqttLogCard from '../components/MqttLogCard'
import NodeOverviewCard from '../components/NodeOverviewCard'

interface Props {
  status: ServerStatus | null
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
  siriusTilkoblet: boolean
  onChannelClick: (index: number) => void
  onMqttClick?: (topic: string) => void
  onHubClick?: (nodeId: string, namn: string) => void
  hubKanalar?: HubKanal[]
  isHubMode?: boolean
}

/**
 * Responsivt kort-rutenett: to kolonnar frå xl og opp, éi under. Breie kort
 * (tabellar, loggar, lister) spenner begge kolonnane; kompakte statuskort
 * deler radene. Vertikal avstand kjem frå korta sin eigen mb; horisontal
 * frå gap-x. Slik utnyttar dashbordet breidda i staden for å stable alt
 * smalt midt på sida.
 */
function Rutenett({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 xl:grid-cols-2 gap-x-4 items-start">{children}</div>
}
function Vid({ children }: { children: ReactNode }) {
  return <div className="xl:col-span-2">{children}</div>
}

export default function DashboardPage({ status, kanalar, liveData, mqttStatus, siriusTilkoblet, onChannelClick, onMqttClick, onHubClick, hubKanalar, isHubMode }: Props) {
  const channelCard = (
    <ChannelLiveCard
      kanalar={kanalar}
      liveData={liveData}
      mqttStatus={mqttStatus}
      siriusTilkoblet={siriusTilkoblet}
      onChannelClick={onChannelClick}
      onMqttClick={onMqttClick}
      onHubClick={onHubClick}
      hubKanalar={hubKanalar}
    />
  )

  // Hub-modus: node-oversikt + mottatte kanalar er breie; server er kompakt.
  if (isHubMode) {
    return (
      <Rutenett>
        <Vid><NodeOverviewCard /></Vid>
        <Vid>{channelCard}</Vid>
        <ServerStatusCard status={status} />
        <Vid><LogViewer /></Vid>
      </Rutenett>
    )
  }

  // Direkte-modus: kompakte statuskort deler radene; kanaltabell, hendingar,
  // MQTT-logg og logg spenner full breidde.
  return (
    <Rutenett>
      <SiriusStatusCard />
      <UsbIpCard ip={status?.ip || '-'} />
      <Vid>{channelCard}</Vid>
      <OpenDaqBridgeCard />
      <RemoteBufferStatusCard />
      <Vid><EventListCard /></Vid>
      <Vid><MqttLogCard /></Vid>
      <ServerStatusCard status={status} />
      <Vid><LogViewer /></Vid>
    </Rutenett>
  )
}
