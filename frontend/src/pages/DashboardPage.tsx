import { useCallback, type ReactNode } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchEnhetKonfig } from '../api/enhet'
import type { ServerStatus, KanalKonfig, KanalLive, MqttStatus, HubKanal, EnhetKonfig } from '../api/types'
import SiriusStatusCard from '../components/SiriusStatusCard'
import UsbIpCard from '../components/UsbIpCard'
import ChannelLiveCard from '../components/ChannelLiveCard'
import InstrumentMeterGrid from '../components/InstrumentMeterGrid'
import HubMeterGrid from '../components/HubMeterGrid'
import OpenDaqBridgeCard from '../components/OpenDaqBridgeCard'
import ServerStatusCard from '../components/ServerStatusCard'
import LogViewer from '../components/LogViewer'
import { RemoteBufferStatusCard } from '../components/BufferStatusCard'
import EventListCard from '../components/EventListCard'
import MqttLogCard from '../components/MqttLogCard'
import NodeOverviewCard from '../components/NodeOverviewCard'
import HubKanalFilterCard from '../components/HubKanalFilterCard'

interface Props {
  status: ServerStatus | null
  kanalar: KanalKonfig[] | null
  liveData: KanalLive | null
  mqttStatus?: MqttStatus | null
  siriusTilkoblet: boolean
  onChannelClick: (index: number) => void
  onMqttClick?: (topic: string) => void
  onHubClick?: (nodeId: string, namn: string) => void
  hubKanalar?: HubKanal[]
  isHubMode?: boolean
}

/**
 * Responsivt kort-rutenett: to kolonnar frå xl og opp, éi under. Breie kort
 * (tabellar, loggar, lister) spenner begge kolonnane; kompakte statuskort
 * deler radene. Vertikal avstand kjem frå korta sin eigen mb; horisontal
 * frå gap-x. Slik utnyttar dashbordet breidda i staden for å stable alt
 * smalt midt på sida.
 */
function Rutenett({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 xl:grid-cols-2 gap-x-4 items-start">{children}</div>
}
function Vid({ children }: { children: ReactNode }) {
  return <div className="xl:col-span-2">{children}</div>
}

export default function DashboardPage({ status, kanalar, liveData, mqttStatus, siriusTilkoblet, onChannelClick, onMqttClick, onHubClick, hubKanalar, isHubMode }: Props) {
  const channelCard = (
    <ChannelLiveCard
      kanalar={kanalar}
      liveData={liveData}
      mqttStatus={mqttStatus}
      siriusTilkoblet={siriusTilkoblet}
      onChannelClick={onChannelClick}
      onMqttClick={onMqttClick}
      onHubClick={onHubClick}
      hubKanalar={hubKanalar}
    />
  )

  // Hub-modus: hero-målarrutenett over dei synlege node-kanalane øvst,
  // deretter node-oversikt, filter, tabell og logg.
  if (isHubMode) {
    return (
      <div className="flex flex-col gap-6">
        <div>
          <HubMeterGrid hubKanalar={hubKanalar} onHubClick={onHubClick} />
        </div>
        <Rutenett>
          <Vid><NodeOverviewCard /></Vid>
          <Vid><HubKanalFilterCard /></Vid>
          <Vid>{channelCard}</Vid>
          <ServerStatusCard status={status} />
          <Vid><LogViewer /></Vid>
        </Rutenett>
      </div>
    )
  }

  // Direkte-modus: hero-målarrutenett øvst, deretter kompakte statuskort og
  // breie kort (kanaltabell, hendingar, MQTT-logg, logg).
  const meterGrid = (
    <InstrumentMeterGrid
      kanalar={kanalar}
      liveData={liveData}
      mqttStatus={mqttStatus}
      siriusTilkoblet={siriusTilkoblet}
      onChannelClick={onChannelClick}
      onMqttClick={onMqttClick}
      hubKanalar={hubKanalar}
      onHubClick={onHubClick}
    />
  )
  return <DirekteDashboard status={status} siriusTilkoblet={siriusTilkoblet} channelCard={channelCard} meterGrid={meterGrid} />
}

function DirekteDashboard({ status, siriusTilkoblet, channelCard, meterGrid }:
  { status: ServerStatus | null; siriusTilkoblet: boolean; channelCard: ReactNode; meterGrid: ReactNode }) {
  const enhetFetcher = useCallback(() => fetchEnhetKonfig(), [])
  const { data: enhet } = usePolling<EnhetKonfig>(enhetFetcher, 30000)
  // USB-instrument til stades? SIRIUS tilkobla, eller USB-einingar oppdaga.
  const usbTilstades = siriusTilkoblet || (status?.usb_enheter?.length ?? 0) > 0
  const visUsb = enhet?.vis_usb === 'vis' || (enhet?.vis_usb !== 'skjul' && usbTilstades)

  return (
    <div className="flex flex-col gap-6">
      <div>{meterGrid}</div>
      <Rutenett>
        {visUsb && <SiriusStatusCard />}
        {visUsb && <UsbIpCard ip={status?.ip || '-'} />}
        <Vid>{channelCard}</Vid>
        <OpenDaqBridgeCard />
        <RemoteBufferStatusCard />
        <Vid><EventListCard /></Vid>
        <Vid><MqttLogCard /></Vid>
        <ServerStatusCard status={status} />
        <Vid><LogViewer /></Vid>
      </Rutenett>
    </div>
  )
}
