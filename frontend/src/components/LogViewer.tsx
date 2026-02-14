import { useCallback, useRef, useEffect } from 'react'
import { fetchLogg } from '../api/logg'
import { usePolling } from '../hooks/usePolling'

export default function LogViewer() {
  const fetcher = useCallback(() => fetchLogg(200), [])
  const { data } = usePolling(fetcher, 5000)
  const preRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight
    }
  }, [data])

  const lines = data?.linjer ?? []

  return (
    <div className="kort">
      <h2>Logg</h2>
      {data?.feil ? (
        <p style={{ color: '#6b6b6b', fontSize: '0.85rem' }}>{data.feil}</p>
      ) : (
        <pre ref={preRef} className="log-viewer">
          {lines.length > 0 ? lines.join('\n') : '(ingen logg-data)'}
        </pre>
      )}
    </div>
  )
}
