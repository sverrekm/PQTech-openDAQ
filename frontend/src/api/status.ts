import { apiGet } from './client'
import type { ServerStatus } from './types'

export const fetchStatus = () => apiGet<ServerStatus>('/api/status')
