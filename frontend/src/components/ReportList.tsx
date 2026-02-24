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
    <div className="mt-3">
      <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out" onClick={hent}>
        Vis rapporter
      </button>
      {loaded && (
        <ul className="list-none p-0 mt-2">
          {rapporter.length > 0 ? (
            rapporter.map((r, i) => (
              <li key={i} className="font-mono text-sm text-gray-500 py-1">
                <a
                  href={`/api/probe/last-ned/${r.filnavn}`}
                  download
                  className="text-[#D76428] hover:underline"
                >
                  {r.filnavn}
                </a>
                <span className="ml-2">
                  ({(r.storrelse / 1024).toFixed(1)} KB)
                </span>
              </li>
            ))
          ) : (
            <li className="text-gray-500">Ingen rapporter</li>
          )}
        </ul>
      )}
    </div>
  )
}
