import { apiPost } from './client'
import type { DebugResult } from './types'

export const sendDebugKommando = (kommando: string, poll = false) =>
  apiPost<DebugResult>('/api/debug/kommando', { kommando, poll })
