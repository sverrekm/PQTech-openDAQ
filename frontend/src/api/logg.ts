import { apiGet } from './client'
import type { LoggResult } from './types'

export const fetchLogg = (antall = 200) =>
  apiGet<LoggResult>(`/api/logg?antall=${antall}`)
