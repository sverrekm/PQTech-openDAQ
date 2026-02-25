import { useState, useCallback } from 'react'
import { fetchOpenDaqStatus, restartOpenDaq } from '../api/opendaq'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'
import CopyableCommand from './CopyableCommand'

function PortStatusDot({ ok }: { ok?: boolean }) {
  if (ok === undefined) return <span className="text-gray-500">?</span>
  return (
    <span className={`inline-block w-2.5 h-2.5 rounded-full mr-1.5 shadow-lg ${ok ? 'bg-green-500 shadow-green-500/50' : 'bg-red-500 shadow-red-500/50'}`} />
  )
}

export default function OpenDaqBridgeCard() {
  const fetcher = useCallback(() => fetchOpenDaqStatus(), [])
  const { data: s, refresh, loading } = usePolling(fetcher, 3000)
  const [restartMsg, setRestartMsg] = useState<{ text: string; ok: boolean } | null>(null)

  if (loading) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm flex justify-center items-center h-48">
        <svg className="animate-spin h-8 w-8 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

  if (!s) return null

  const handleRestart = async () => {
    try {
      const res = await restartOpenDaq()
      setRestartMsg({ text: res.melding, ok: res.suksess })
      refresh()
    } catch (e) {
      setRestartMsg({ text: String(e), ok: false })
    }
  }

  const ps = s.port_status
  const alleOppe = s.alle_portar_oppe === true

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">openDAQ Nettverksservere</h2>

      {/* Hovudstatus med visuell indikator */}
      <div className={`flex items-center gap-2 p-2 rounded-lg mb-3 ${alleOppe ? 'bg-green-100 border border-green-500' : s.aktiv ? 'bg-yellow-100 border border-yellow-500' : 'bg-red-100 border border-red-500'}`}>
        <span className={`inline-block w-3.5 h-3.5 rounded-full ${alleOppe ? 'bg-green-500 shadow-lg shadow-green-500/50' : s.aktiv ? 'bg-yellow-500' : 'bg-red-500'}`} />
        <span className={`font-semibold ${alleOppe ? 'text-green-700' : s.aktiv ? 'text-yellow-700' : 'text-red-700'}`}>
          {alleOppe ? 'Alle servere oppe' : s.aktiv ? 'Delvis aktiv' : 'Inaktiv'}
        </span>
        {s.startet && alleOppe && (
          <span className="text-xs text-gray-500 ml-auto">
            Sidan {new Date(s.startet).toLocaleTimeString('nb-NO')}
          </span>
        )}
      </div>

      <InfoGrid items={[
        { label: 'Enhet', value: s.enhet_namn || '-' },
        { label: 'Kanalar', value: s.kanalar?.length || '-' },
      ]} />

      {s.feil && !s.aktiv && (
        <div className="mt-3 px-3 py-2 rounded-lg text-sm bg-red-100 text-red-800">{s.feil}</div>
      )}

      {/* Per-server port-status */}
      {ps && (
        <div className="my-2">
          <div className="text-sm text-gray-500 mb-1.5">
            Live port-verifisering:
          </div>
          {[
            { namn: 'OPC-UA', key: 'opcua' as const, port: s.porter?.opcua || 4840 },
            { namn: 'Native Streaming', key: 'native_streaming' as const, port: s.porter?.native_streaming || 7420 },
            { namn: 'WebSocket/LT', key: 'websocket' as const, port: s.porter?.websocket || 7414 },
          ].map(srv => (
            <div key={srv.key} className="flex items-center py-1 text-sm">
              <PortStatusDot ok={ps[srv.key]} />
              <span className="min-w-[100px]">{srv.namn}</span>
              <span className="text-gray-500 text-xs ml-auto">
                {s.ip ? `${s.ip}:${srv.port}` : `:${srv.port}`}
              </span>
            </div>
          ))}
        </div>
      )}

      {s.aktiv && s.ip && (
        <>
          <CopyableCommand text={s.ip} className="mt-3" />
          <p className="text-gray-500 text-xs mt-2">
            Eininga dukkar automatisk opp som "Detected device" i DewesoftX Setup &gt; Devices.
          </p>
        </>
      )}
      <div className="mt-3">
        <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out" onClick={handleRestart}>Start på nytt</button>
        {restartMsg && (
          <span className={`text-sm ml-2 ${restartMsg.ok ? 'text-green-700' : 'text-red-700'}`}>
            {restartMsg.text}
          </span>
        )}
      </div>
    </div>
  )
}
