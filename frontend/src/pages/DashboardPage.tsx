import SiriusStatusCard from '../components/SiriusStatusCard'
import ChannelLiveCard from '../components/ChannelLiveCard'
import OpenDaqBridgeCard from '../components/OpenDaqBridgeCard'
import ServerStatusCard from '../components/ServerStatusCard'
import LogViewer from '../components/LogViewer'

export default function DashboardPage() {
  return (
    <>
      <SiriusStatusCard />
      <ChannelLiveCard />
      <OpenDaqBridgeCard />
      <ServerStatusCard />
      <LogViewer />
    </>
  )
}
