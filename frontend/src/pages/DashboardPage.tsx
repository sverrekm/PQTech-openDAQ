import type { ServerStatus, KanalKonfig, KanalLive, MqttStatus } from '../api/types'
import SiriusStatusCard from '../components/SiriusStatusCard'
import UsbIpCard from '../components/UsbIpCard'
import ChannelLiveCard from '../components/ChannelLiveCard'
import OpenDaqBridgeCard from '../components/OpenDaqBridgeCard'
import ServerStatusCard from '../components/ServerStatusCard'
import LogViewer from '../components/LogViewer'
import UpdateCard from '../components/UpdateCard'

interface Props {
  status: ServerStatus | null
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
  siriusTilkoblet: boolean
  onChannelClick: (index: number) => void
}

export default function DashboardPage({ status, kanalar, liveData, mqttStatus, siriusTilkoblet, onChannelClick }: Props) {
  return (
    <>
      <SiriusStatusCard />
      <UsbIpCard ip={status?.ip || '-'} />
      <ChannelLiveCard kanalar={kanalar} liveData={liveData} mqttStatus={mqttStatus} siriusTilkoblet={siriusTilkoblet} onChannelClick={onChannelClick} />
      <OpenDaqBridgeCard />
      <ServerStatusCard status={status} />
      <LogViewer />
      <UpdateCard />
    </>
  )
}
