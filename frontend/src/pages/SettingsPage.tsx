import DeviceSettingsCard from '../components/DeviceSettingsCard'
import ChannelConfigCard from '../components/ChannelConfigCard'
import MqttSettingsCard from '../components/MqttSettingsCard'
import TailscaleCard from '../components/TailscaleCard'
import HubNodeConfigCard from '../components/HubNodeConfigCard'
import DeviceConnectionCard from '../components/DeviceConnectionCard'
import DebugConsoleCard from '../components/DebugConsoleCard'
import ProbeAnalysisCard from '../components/ProbeAnalysisCard'
import Ep2RecoveryCard from '../components/Ep2RecoveryCard'
import BufferConfigCard from '../components/BufferConfigCard'

export default function SettingsPage() {
  return (
    <>
      <DeviceSettingsCard />
      <ChannelConfigCard />
      <BufferConfigCard />
      <MqttSettingsCard />
      <TailscaleCard />
      <HubNodeConfigCard />
      <DeviceConnectionCard />
      <DebugConsoleCard />
      <ProbeAnalysisCard />
      <Ep2RecoveryCard />
    </>
  )
}
