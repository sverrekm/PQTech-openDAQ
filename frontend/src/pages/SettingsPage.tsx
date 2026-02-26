import DeviceSettingsCard from '../components/DeviceSettingsCard'
import ChannelConfigCard from '../components/ChannelConfigCard'
import MqttSettingsCard from '../components/MqttSettingsCard'
import DeviceConnectionCard from '../components/DeviceConnectionCard'
import DebugConsoleCard from '../components/DebugConsoleCard'
import ProbeAnalysisCard from '../components/ProbeAnalysisCard'
import Ep2RecoveryCard from '../components/Ep2RecoveryCard'

export default function SettingsPage() {
  return (
    <>
      <DeviceSettingsCard />
      <ChannelConfigCard />
      <MqttSettingsCard />
      <DeviceConnectionCard />
      <DebugConsoleCard />
      <ProbeAnalysisCard />
      <Ep2RecoveryCard />
    </>
  )
}
