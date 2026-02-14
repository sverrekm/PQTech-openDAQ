import { useCallback } from 'react'
import { fetchStatus } from '../api/status'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'

export default function ServerStatusCard() {
  const fetcher = useCallback(() => fetchStatus(), [])
  const { data: s } = usePolling(fetcher, 5000)

  if (!s) return null

  return (
    <div className="kort">
      <h2>Server</h2>
      <InfoGrid items={[
        { label: 'IP-adresse', value: s.ip || '-' },
        { label: 'Enhet', value: s.enhet_navn || 'Soker...' },
        { label: 'Protokoller', value: s.servere.length > 0 ? s.servere.join(', ') : '-' },
        { label: 'Kanaler', value: s.kanaler.length > 0 ? s.kanaler.length : '-' },
      ]} />
      {s.kanaler.length > 0 && (
        <div className="kanal-liste">
          {s.kanaler.map((k, i) => (
            <span key={i} className="tag tag-aktiv">{k}</span>
          ))}
        </div>
      )}
    </div>
  )
}
