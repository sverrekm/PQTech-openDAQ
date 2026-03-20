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

interface Props {
  status: ServerStatus | null
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
  siriusTilkoblet: boolean
  onChannelClick: (index: number) => void
  hubKanalar?: HubKanal[]
  isHubMode?: boolean
}

export default function DashboardPage({ status, kanalar, liveData, mqttStatus, siriusTilkoblet, onChannelClick, hubKanalar, isHubMode }: Props) {
  return (
    <>
      {!isHubMode && <SiriusStatusCard />}
      {!isHubMode && <UsbIpCard ip={status?.ip || '-'} />}
      <ChannelLiveCard kanalar={kanalar} liveData={liveData} mqttStatus={mqttStatus} siriusTilkoblet={siriusTilkoblet} onChannelClick={onChannelClick} hubKanalar={hubKanalar} />
      {!isHubMode && <OpenDaqBridgeCard />}
      {!isHubMode && <RemoteBufferStatusCard />}
      {!isHubMode && <EventListCard />}
      {!isHubMode && <MqttLogCard />}
      <ServerStatusCard status={status} />
      <LogViewer />
    </>
  )
}
