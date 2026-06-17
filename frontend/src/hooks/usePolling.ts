import { useState, useEffect, useRef, useCallback } from 'react'

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled = true,
): { data: T | null; error: string | null; loading: boolean; refresh: () => void; stale: boolean; lastOk: number | null } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [stale, setStale] = useState(false)
  const [lastOk, setLastOk] = useState<number | null>(null)
  const lastOkRef = useRef<number | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const doFetch = useCallback(async () => {
    try {
      const result = await fetcher()
      setData(result)
      setError(null)
      const no = Date.now()
      lastOkRef.current = no
      setLastOk(no)
      setStale(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [fetcher])

  useEffect(() => {
    if (!enabled) return
    doFetch()
    timerRef.current = setInterval(doFetch, intervalMs)
    // Stale-vakt: marker data som forelda om ingen vellukka henting på ei
    // stund (2.5x intervall, minst 6 s). Tikkar kvart sekund så UI-et kan
    // reagere sjølv om hentinga heng/feilar.
    const staleMs = Math.max(intervalMs * 2.5, 6000)
    const vakt = setInterval(() => {
      if (lastOkRef.current !== null && Date.now() - lastOkRef.current > staleMs) {
        setStale(true)
      }
    }, 1000)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      clearInterval(vakt)
    }
  }, [doFetch, intervalMs, enabled])

  return { data, error, loading, refresh: doFetch, stale, lastOk }
}
