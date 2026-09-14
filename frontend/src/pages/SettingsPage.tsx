import type { ReactNode } from 'react'
import DeviceNameCard from '../components/DeviceNameCard'
import DeviceSettingsCard from '../components/DeviceSettingsCard'
import InstrumentFunnCard from '../components/InstrumentFunnCard'
import NodeTjenesterCard from '../components/NodeTjenesterCard'
import PqzipArkivCard from '../components/PqzipArkivCard'
import ChannelConfigCard from '../components/ChannelConfigCard'
import HubConnectionCard from '../components/HubConnectionCard'
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
import SettingsSection, { OPNE_SEKSJON } from '../components/SettingsSection'
import { useI18n } from '../i18n'

interface Kategori {
  id: string
  tittel: string
  sub: string
  defaultOpen?: boolean
  innhald: ReactNode
}

export default function SettingsPage() {
  const { t } = useI18n()

  const kategoriar: Kategori[] = [
    {
      id: 'enhet',
      tittel: t('Device & measurement'),
      sub: t('Name, model, channels and the instruments this box acquires from.'),
      defaultOpen: true,
      innhald: (
        <>
          <DeviceNameCard />
          <DeviceSettingsCard />
          <DeviceConnectionCard />
          <ChannelConfigCard />
          <InstrumentFunnCard />
          <NodeTjenesterCard />
          <PqzipArkivCard />
          <BufferConfigCard />
        </>
      ),
    },
    {
      id: 'deling',
      tittel: t('Sharing & integrations'),
      sub: t('Publish channels over MQTT, to Grafana/InfluxDB and the read API.'),
      innhald: (
        <>
          <HubConnectionCard />
          <MqttSettingsCard />
          <MqttOppdagCard />
          <InfluxShareCard />
          {/* EMC: på SIRIUS-direkte node frå lokal ADC; på hubben frå dei
              bridga bølgjeformene (hub_emc). Synleg i begge modus. */}
          <EmcCard />
          <ApiKeysCard />
        </>
      ),
    },
    {
      id: 'lagring',
      tittel: t('Storage'),
      sub: t('NAS mount, raw-file archive and the hub time-series database.'),
      innhald: <StorageCard />,
    },
    {
      id: 'nettverk',
      tittel: t('Network & nodes'),
      sub: t('Wi-Fi, instrument network, scanning, FTP and hub/node topology.'),
      innhald: (
        <>
          <WifiCard />
          <InstrumentNettverkCard />
          <NettSkannCard />
          <InstrumentFtpCard />
          <TailscaleCard />
          <HubNodeConfigCard />
        </>
      ),
    },
    {
      id: 'diagnose',
      tittel: t('Diagnostics (advanced)'),
      sub: t('Console, probe analysis and endpoint recovery. Handle with care.'),
      innhald: (
        <>
          <DebugConsoleCard />
          <ProbeAnalysisCard />
          <Ep2RecoveryCard />
        </>
      ),
    },
  ]

  const tal = (n: number) => String(n + 1).padStart(2, '0')

  const gaTil = (id: string) =>
    window.dispatchEvent(new CustomEvent(OPNE_SEKSJON, { detail: id }))

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
      {/* Sticky nummerert index (venstre) */}
      <aside className="hidden lg:block lg:col-span-3 sticky" style={{ top: 24 }}>
        <span className="mono block text-[9px] tracking-[0.16em] uppercase" style={{ color: 'var(--color-accent-700)' }}>
          {t('Configuration')}
        </span>
        <h2 className="text-[27px] leading-tight mt-0.5 mb-4" style={{ fontFamily: 'var(--font-heading)', fontWeight: 600 }}>
          {t('Settings')}
        </h2>
        {kategoriar.map((k, i) => (
          <button
            key={k.id}
            onClick={() => gaTil(k.id)}
            className="w-full flex items-center gap-2.5 text-left px-2.5 py-2 border-t border-l-[3px] border-l-transparent hover:border-l-[#D76428] transition-colors group"
            style={{ borderTopColor: 'var(--color-divider)' }}
          >
            <span className="mono text-[11px]" style={{ color: 'rgba(26,26,26,0.4)' }}>{tal(i)}</span>
            <span className="text-[16px] uppercase tracking-[0.02em] group-hover:text-[#954217]" style={{ fontFamily: 'var(--font-heading)', fontWeight: 600 }}>
              {k.tittel}
            </span>
          </button>
        ))}
        <div style={{ borderTop: '1px solid var(--color-divider)' }} />
      </aside>

      {/* Seksjonar (høgre) */}
      <div className="lg:col-span-9 min-w-0">
        {kategoriar.map((k, i) => (
          <SettingsSection key={k.id} id={k.id} num={tal(i)} tittel={k.tittel} sub={k.sub} defaultOpen={k.defaultOpen}>
            {k.innhald}
          </SettingsSection>
        ))}
      </div>
    </div>
  )
}
