// --- /api/status ---
export interface ServerStatus {
  server_kjorer: boolean
  ip: string
  enhet_navn: string
  kanaler: string[]
  servere: string[]
  usb_enheter: string[]
  siste_maaling: string | null
  antall_maalinger: number
  autonom: boolean
}

// --- /api/sirius/* ---
export interface SiriusStatus {
  tilgjengelig: boolean
  tilkoblet?: boolean
  streamer?: boolean
  serienummer?: string
  data_rate_kbs?: number
  ep2_ok?: boolean
  slot_info?: SlotInfo[]
  melding?: string
  feil?: string
}

export interface SlotInfo {
  kanal: number
  aktiv: boolean
}

export interface SiriusData {
  [key: string]: {
    siste: number | null
    antall: number
  }
}

export interface ActionResult {
  suksess: boolean
  melding: string
}

// --- /api/opendaq/* ---
export interface OpenDaqStatus {
  tilgjengelig?: boolean
  aktiv?: boolean
  enhet_namn?: string
  kanalar?: string[]
  servere?: string[]
  ip?: string
  porter?: {
    opcua?: number
    native_streaming?: number
    websocket?: number
  }
  port_status?: {
    opcua?: boolean
    native_streaming?: boolean
    websocket?: boolean
  }
  alle_portar_oppe?: boolean
  startet?: string
  feil?: string
  melding?: string
}

export interface OpenDaqVerdiarEntry {
  siste: number
  kjelde?: string
}

export interface OpenDaqVerdiar {
  [key: string]: OpenDaqVerdiarEntry | { data_teller: number; sirius_aktiv: boolean } | undefined
}

// --- /api/kanalar ---
export interface KanalKonfig {
  indeks: number
  namn: string
  aktiv: boolean
  type: string
  range_min: number
  range_max: number
  enhet: string
  sample_rate: number
  // Sensor-skalering (to-punkt lineaer)
  sensor_aktiv: boolean
  sensor_namn: string
  sensor_inn_1: number
  sensor_ut_1: number
  sensor_inn_2: number
  sensor_ut_2: number
  sensor_enhet: string
  excitation_v: number
}

export interface KanalLive {
  opendaq?: {
    [key: string]: {
      siste: number
      rms?: number
      topp?: number
      snitt?: number
      antall?: number
      kjelde?: string
    }
  }
  driver?: {
    [key: string]: {
      siste: number | null
      antall: number
    }
  }
}

// --- /api/tailscale/* ---
export interface TailscaleNode {
  hostname: string
  ip: string
  online: boolean
}

export interface TailscaleStatus {
  installert: boolean
  daemon_køyrer: boolean
  tilkobla: boolean
  ip: string | null
  hostname: string
  tailnet: string
  nodar: TailscaleNode[]
  feil: string | null
}

// --- /api/usbip/* ---
export interface UsbIpStatus {
  sirius_paa_usb: boolean
  sirius_enhet_funnet?: string
  sirius_busid_funnet?: string
  busid?: string
  deling_aktiv: boolean
  tilgjengelig: boolean
  feil?: string
}

// --- /api/probe/* ---
export interface ProbeResult {
  status: 'idle' | 'running' | 'done' | 'error'
  output: string
  rapport?: string | null
  returncode?: number
}

export interface Rapport {
  filnavn: string
  storrelse: number
  endret: number
}

// --- /api/enheter ---
export interface Enhet {
  navn: string
  tilkobling: string
}

// --- /api/logg ---
export interface LoggResult {
  linjer: string[]
  antall: number
  feil?: string
}

// --- /api/debug ---
export interface DebugResult {
  svar?: string
  hex?: string
  feil?: string
}

// --- /api/mqtt/* ---
export interface MqttBrokerKonfig {
  host: string
  port: number
  brukarnavn: string
  passord: string
  klient_id: string
  aktivert: boolean
}

export interface MqttKanalKonfig {
  topic: string
  namn: string
  enhet: string
  json_sti: string
  range_min: number
  range_max: number
}

export interface MqttKonfig {
  broker: MqttBrokerKonfig
  kanalar: MqttKanalKonfig[]
}

export interface MqttTopicStatus {
  namn: string
  enhet: string
  verdi: number | null
  sist_oppdatert: number | null
}

export interface MqttStatus {
  tilkobla: boolean
  aktivert: boolean
  broker: string
  feil?: string
  meldingar_motteke: number
  topics: Record<string, MqttTopicStatus>
}

// --- /api/enhet/* ---
export interface EnhetKonfig {
  antal_adc_kanalar: number
  modell: string
  location: string
}

// --- /api/hub/* ---
export interface HubNodeStatus {
  id: string
  namn: string
  adresse: string
  port: number
  protokoll: string
  lokasjon: string
  aktivert: boolean
  tilkobla: boolean
  feil: string | null
  sist_sett: string | null
  tilkobla_sidan: string | null
  antal_kanalar: number
}

export interface HubStatus {
  modus: string
  aktiv?: boolean
  startet: string | null
  totalt_kanalar: number
  totalt_nodar: number
  tilkobla_nodar: number
  nodar: HubNodeStatus[]
  ip: string
}

export interface HubKonfig {
  nodar: Array<{
    id: string
    namn: string
    adresse: string
    port: number
    aktivert: boolean
    protokoll: string
    lokasjon: string
  }>
  reconnect_intervall: number
  helsesjekk_intervall: number
}

export interface HubNodeResult {
  suksess: boolean
  melding: string
  node?: {
    id: string
    namn: string
    adresse: string
    port: number
    aktivert: boolean
    protokoll: string
    lokasjon: string
  }
}

// --- /api/hub/kanalar ---
export interface HubKanal {
  node_id: string
  node_namn: string
  namn: string
  verdi: number | null
  eining: string
}

// --- /api/system/* ---
export interface VersjonInfo {
  sha: string
  melding: string
  dato: string
}

export interface OppdateringSjekk {
  noverande: VersjonInfo
  github: { sha: string; melding: string; dato: string; forfattar: string }
  oppdatering_tilgjengeleg: boolean
  feil?: string
}

export interface OppdateringsResultat {
  suksess: boolean
  versjon?: string
  melding?: string
  oppdaterte_filer?: string[]
  feil?: string
}

// --- /api/buffer/* ---
export interface BufferStatus {
  aktivert: boolean
  totalt_rader: number
  usynkroniserte?: number
  synkroniserte?: number
  storleik_mb?: number
  maks_storleik_mb?: number
  eldste_ts?: number | null
  nyaste_ts?: number | null
  skalering_klar?: boolean
  skriv_per_sek?: number
  lagringssti?: string
  ssd_aktiv?: boolean
  ram_buffer_storleik_mb?: number
  hendingar_totalt?: number
  mqtt_logg_rader?: number
  sample_rate?: number
  feil?: string
}

export interface BufferKonfig {
  aktivert: boolean
  intervall_ms: number
  maks_storleik_mb: number
  bevar_usynkronisert: boolean
  hub_sync_intervall_sek: number
  hub_batch_storleik: number
  hub_retensjon_dagar: number
  sample_rate: number
  ssd_sti: string
  ram_buffer_sekund: number
  hendingar_aktivert: boolean
  rms_terskel_prosent: number
  dvdt_terskel: number
  mqtt_endring_terskel: number
  pre_trigger_ms: number
  post_trigger_ms: number
  mqtt_logg_aktivert: boolean
}

export interface BufferHending {
  id: number
  tidsstempel_ms: number
  type: string
  skildring: string
  varigheit_ms: number
  sample_rate: number
}

export interface MqttLoggRad {
  id: number
  tidsstempel_ms: number
  topic: string
  verdi: number
}

export interface LagringsInfo {
  sti: string
  ssd_aktiv: boolean
  ledig_mb: number
  brukt_mb: number
}

// --- /api/hub/buffer/* ---
export interface HubBufferNodeSync {
  node_id: string
  siste_fjern_id: number
  siste_sync: string | null
  antal_henta: number
  rader_i_hub: number
}

export interface HubBufferStatus {
  aktivert: boolean
  totalt_rader?: number
  storleik_mb?: number
  eldste_ts?: number | null
  nyaste_ts?: number | null
  retensjon_dagar?: number
  sync_intervall_sek?: number
  nodar?: HubBufferNodeSync[]
  hendingar_totalt?: number
  mqtt_logg_rader?: number
  feil?: string
}
