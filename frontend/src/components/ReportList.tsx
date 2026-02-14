import { useState } from 'react'
import { fetchRapporter } from '../api/probe'
import type { Rapport } from '../api/types'

export default function ReportList() {
  const [rapporter, setRapporter] = useState<Rapport[]>([])
  const [loaded, setLoaded] = useState(false)

  const hent = async () => {
    try {
      const data = await fetchRapporter()
      setRapporter(data.rapporter || [])
      setLoaded(true)
    } catch {
      setRapporter([])
      setLoaded(true)
    }
  }

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <button className="btn btn-blaa" onClick={hent} style={{ fontSize: '0.8rem' }}>
        Vis rapporter
      </button>
      {loaded && (
        <ul className="enhet-liste" style={{ marginTop: '0.5rem' }}>
          {rapporter.length > 0 ? (
            rapporter.map((r, i) => (
              <li key={i} style={{ fontFamily: "'Consolas', monospace", fontSize: '0.85rem', color: '#6b6b6b' }}>
                <a
                  href={`/api/probe/last-ned/${r.filnavn}`}
                  download
                  style={{ color: '#D76428' }}
                >
                  {r.filnavn}
                </a>
                <span style={{ marginLeft: '0.5rem' }}>
                  ({(r.storrelse / 1024).toFixed(1)} KB)
                </span>
              </li>
            ))
          ) : (
            <li style={{ color: '#6b6b6b' }}>Ingen rapporter</li>
          )}
        </ul>
      )}
    </div>
  )
}
