import { apiGet, apiPost } from './client'
import type { UsbIpStatus } from './types'

export const fetchUsbIpStatus = () => apiGet<UsbIpStatus>('/api/usbip/status')

interface UsbIpActionResult {
  suksess: boolean
  melding: string
  status?: UsbIpStatus
}

export const usbipDel = () => apiPost<UsbIpActionResult>('/api/usbip/del')

export const usbipStopp = () => apiPost<UsbIpActionResult>('/api/usbip/stopp')
