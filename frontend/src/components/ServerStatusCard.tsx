import type { ServerStatus } from '../api/types'
import InfoGrid from './InfoGrid'

interface Props {
  status: ServerStatus | null
}

export default function ServerStatusCard({ status: s }: Props) {
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
