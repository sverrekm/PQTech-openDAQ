import type { ServerStatus } from '../api/types'
import ChannelConfigCard from '../components/ChannelConfigCard'
import UsbIpCard from '../components/UsbIpCard'
import DeviceConnectionCard from '../components/DeviceConnectionCard'
import DebugConsoleCard from '../components/DebugConsoleCard'
import ProbeAnalysisCard from '../components/ProbeAnalysisCard'
import Ep2RecoveryCard from '../components/Ep2RecoveryCard'

interface Props {
  status: ServerStatus | null
}

export default function SettingsPage({ status }: Props) {
  return (
    <>
      <ChannelConfigCard />
      <UsbIpCard ip={status?.ip || '-'} />
      <DeviceConnectionCard />
      <DebugConsoleCard />
      <ProbeAnalysisCard />
      <Ep2RecoveryCard />
    </>
  )
}
