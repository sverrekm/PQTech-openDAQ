import type { ServerStatus } from '../api/types'
import SiriusStatusCard from '../components/SiriusStatusCard'
import ChannelLiveCard from '../components/ChannelLiveCard'
import OpenDaqBridgeCard from '../components/OpenDaqBridgeCard'
import ServerStatusCard from '../components/ServerStatusCard'
import LogViewer from '../components/LogViewer'

interface Props {
  status: ServerStatus | null
}

export default function DashboardPage({ status }: Props) {
  return (
    <>
      <SiriusStatusCard />
      <ChannelLiveCard />
      <OpenDaqBridgeCard />
      <ServerStatusCard status={status} />
      <LogViewer />
    </>
  )
}
