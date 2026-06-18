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
import InfluxShareCard from '../components/InfluxShareCard'
import EmcCard from '../components/EmcCard'
import HubStorageCard from '../components/HubStorageCard'
import SettingsSection from '../components/SettingsSection'
import { useI18n } from '../i18n'

export default function SettingsPage() {
  const { t } = useI18n()
  return (
    <>
      <SettingsSection id="enhet" tittel={t('Device & measurement')} defaultOpen>
        <DeviceSettingsCard />
        <DeviceConnectionCard />
        <ChannelConfigCard />
        <BufferConfigCard />
      </SettingsSection>

      <SettingsSection id="deling" tittel={t('Sharing & integrations')}>
        <MqttSettingsCard />
        <InfluxShareCard />
        {/* EMC: på SIRIUS-direkte node frå lokal ADC; på hubben frå dei
            bridga bølgjeformene (hub_emc). Synleg i begge modus. */}
        <EmcCard />
        <HubStorageCard />
      </SettingsSection>

      <SettingsSection id="nettverk" tittel={t('Network & nodes')}>
        <TailscaleCard />
        <HubNodeConfigCard />
      </SettingsSection>

      <SettingsSection id="diagnose" tittel={t('Diagnostics (advanced)')}>
        <DebugConsoleCard />
        <ProbeAnalysisCard />
        <Ep2RecoveryCard />
      </SettingsSection>
    </>
  )
}
