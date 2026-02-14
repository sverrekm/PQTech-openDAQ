import { useState, useRef, useCallback, useEffect } from 'react'
import { fetchProbeStatus } from '../api/probe'
import type { ProbeResult } from '../api/types'

export function useProbeRunner() {
  const [status, setStatus] = useState<ProbeResult['status']>('idle')
  const [output, setOutput] = useState('')
  const [returncode, setReturncode] = useState<number | undefined>()
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const startPolling = useCallback(() => {
    stopPolling()
    setStatus('running')
    setOutput('')

    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchProbeStatus()
        if (data.status === 'done' || data.status === 'error') {
          stopPolling()
          setStatus(data.status)
          setOutput(data.output || '(tomt resultat)')
          setReturncode(data.returncode)
        } else if (data.status === 'running') {
          setOutput(data.output || '')
        }
      } catch {
        // Ignore transient errors
      }
    }, 1500)
  }, [stopPolling])

  useEffect(() => stopPolling, [stopPolling])

  return { status, output, returncode, startPolling, stopPolling }
}
