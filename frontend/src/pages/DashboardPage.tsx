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

  // Hub-modus: fokus på node-oversikt (status per måleboks) + mottatte kanalar.
  if (isHubMode) {
    return (
      <>
        <NodeOverviewCard />
        {channelCard}
        <ServerStatusCard status={status} />
        <LogViewer />
      </>
    )
  }

  // Direkte-modus: fokus på SIRIUS-maskinvare, USB og lokale ADC-kanalar.
  return (
    <>
      <SiriusStatusCard />
      <UsbIpCard ip={status?.ip || '-'} />
      {channelCard}
      <OpenDaqBridgeCard />
      <RemoteBufferStatusCard />
      <EventListCard />
      <MqttLogCard />
      <ServerStatusCard status={status} />
      <LogViewer />
    </>
  )
}
