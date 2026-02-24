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
