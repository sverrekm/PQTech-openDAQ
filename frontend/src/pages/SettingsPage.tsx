import DeviceNameCard from '../components/DeviceNameCard'
import DeviceSettingsCard from '../components/DeviceSettingsCard'
import InstrumentFunnCard from '../components/InstrumentFunnCard'
import NodeTjenesterCard from '../components/NodeTjenesterCard'
import PqzipArkivCard from '../components/PqzipArkivCard'
import ChannelConfigCard from '../components/ChannelConfigCard'
import MqttSettingsCard from '../components/MqttSettingsCard'
import MqttOppdagCard from '../components/MqttOppdagCard'
import WifiCard from '../components/WifiCard'
import InstrumentNettverkCard from '../components/InstrumentNettverkCard'
import NettSkannCard from '../components/NettSkannCard'
import InstrumentFtpCard from '../components/InstrumentFtpCard'
import TailscaleCard from '../components/TailscaleCard'
import HubNodeConfigCard from '../components/HubNodeConfigCard'
import DeviceConnectionCard from '../components/DeviceConnectionCard'
import DebugConsoleCard from '../components/DebugConsoleCard'
import ProbeAnalysisCard from '../components/ProbeAnalysisCard'
import Ep2RecoveryCard from '../components/Ep2RecoveryCard'
import BufferConfigCard from '../components/BufferConfigCard'
import InfluxShareCard from '../components/InfluxShareCard'
import ApiKeysCard from '../components/ApiKeysCard'
import EmcCard from '../components/EmcCard'
import StorageCard from '../components/StorageCard'
import SettingsSection from '../components/SettingsSection'
import { useI18n } from '../i18n'

export default function SettingsPage() {
  const { t } = useI18n()
  return (
    <>
      <SettingsSection id="enhet" tittel={t('Device & measurement')} defaultOpen>
        <DeviceNameCard />
        <DeviceSettingsCard />
        <DeviceConnectionCard />
        <ChannelConfigCard />
        <InstrumentFunnCard />
        <NodeTjenesterCard />
        <PqzipArkivCard />
        <BufferConfigCard />
      </SettingsSection>

      <SettingsSection id="deling" tittel={t('Sharing & integrations')}>
        <MqttSettingsCard />
        <MqttOppdagCard />
        <InfluxShareCard />
        {/* EMC: på SIRIUS-direkte node frå lokal ADC; på hubben frå dei
            bridga bølgjeformene (hub_emc). Synleg i begge modus. */}
        <EmcCard />
        <ApiKeysCard />
      </SettingsSection>

      <SettingsSection id="lagring" tittel={t('Storage')}>
        {/* Samla: NAS-montering → rå-fil-arkiv → hub-database */}
        <StorageCard />
      </SettingsSection>

      <SettingsSection id="nettverk" tittel={t('Network & nodes')}>
        <WifiCard />
        <InstrumentNettverkCard />
        <NettSkannCard />
        <InstrumentFtpCard />
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
