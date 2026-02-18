import { useState, useCallback } from 'react'
import { fetchOpenDaqStatus, restartOpenDaq } from '../api/opendaq'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'
import CopyableCommand from './CopyableCommand'

function PortStatusDot({ ok }: { ok?: boolean }) {
  if (ok === undefined) return <span style={{ color: '#6b7280' }}>?</span>
  return (
    <span style={{
      display: 'inline-block',
      width: 10,
      height: 10,
      borderRadius: '50%',
      backgroundColor: ok ? '#10b981' : '#ef4444',
      marginRight: 6,
      boxShadow: ok ? '0 0 6px #10b981' : '0 0 6px #ef4444',
    }} />
  )
}

export default function OpenDaqBridgeCard() {
  const fetcher = useCallback(() => fetchOpenDaqStatus(), [])
  const { data: s, refresh } = usePolling(fetcher, 3000)
  const [restartMsg, setRestartMsg] = useState<{ text: string; ok: boolean } | null>(null)

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
    <div className="kort">
      <h2>openDAQ Nettverksservere</h2>

      {/* Hovudstatus med visuell indikator */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 12px',
        borderRadius: 8,
        marginBottom: 12,
        backgroundColor: alleOppe ? 'rgba(16,185,129,0.1)' : s.aktiv ? 'rgba(234,179,8,0.1)' : 'rgba(239,68,68,0.1)',
        border: `1px solid ${alleOppe ? '#10b981' : s.aktiv ? '#eab308' : '#ef4444'}`,
      }}>
        <span style={{
          display: 'inline-block',
          width: 14,
          height: 14,
          borderRadius: '50%',
          backgroundColor: alleOppe ? '#10b981' : s.aktiv ? '#eab308' : '#ef4444',
          boxShadow: alleOppe ? '0 0 8px #10b981' : undefined,
          animation: alleOppe ? 'none' : undefined,
        }} />
        <span style={{
          fontWeight: 600,
          color: alleOppe ? '#10b981' : s.aktiv ? '#eab308' : '#ef4444',
        }}>
          {alleOppe ? 'Alle servere oppe' : s.aktiv ? 'Delvis aktiv' : 'Inaktiv'}
        </span>
        {s.startet && alleOppe && (
          <span style={{ fontSize: '0.75rem', color: '#6b7280', marginLeft: 'auto' }}>
            Sidan {new Date(s.startet).toLocaleTimeString('nb-NO')}
          </span>
        )}
      </div>

      <InfoGrid items={[
        { label: 'Enhet', value: s.enhet_namn || '-' },
        { label: 'Kanalar', value: s.kanalar?.length || '-' },
      ]} />

      {s.feil && !s.aktiv && (
        <div className="melding melding-feil">{s.feil}</div>
      )}

      {/* Per-server port-status */}
      {ps && (
        <div style={{ margin: '10px 0' }}>
          <div style={{ fontSize: '0.8rem', color: '#9ca3af', marginBottom: 6 }}>
            Live port-verifisering:
          </div>
          {[
            { namn: 'OPC-UA', key: 'opcua' as const, port: s.porter?.opcua || 4840 },
            { namn: 'Native Streaming', key: 'native_streaming' as const, port: s.porter?.native_streaming || 7420 },
            { namn: 'WebSocket/LT', key: 'websocket' as const, port: s.porter?.websocket || 7414 },
          ].map(srv => (
            <div key={srv.key} style={{
              display: 'flex',
              alignItems: 'center',
              padding: '4px 0',
              fontSize: '0.85rem',
            }}>
              <PortStatusDot ok={ps[srv.key]} />
              <span style={{ minWidth: 130 }}>{srv.namn}</span>
              <span style={{ color: '#6b7280', fontSize: '0.75rem' }}>
                {s.ip ? `${s.ip}:${srv.port}` : `:${srv.port}`}
              </span>
            </div>
          ))}
        </div>
      )}

      {s.aktiv && s.ip && (
        <>
          <CopyableCommand text={s.ip} />
          <p style={{ color: '#6b6b6b', fontSize: '0.8rem', marginTop: '0.5rem' }}>
            DewesoftX: Settings &gt; Devices &gt; Dewesoft NET &gt; Manually add &gt; skriv inn adressa over.
          </p>
        </>
      )}
      <div style={{ marginTop: '0.75rem' }}>
        <button className="btn btn-blaa" onClick={handleRestart}>Restart bridge</button>
        {restartMsg && (
          <span style={{
            fontSize: '0.8rem',
            marginLeft: '0.5rem',
            color: restartMsg.ok ? '#10b981' : '#ef4444',
          }}>
            {restartMsg.text}
          </span>
        )}
      </div>
    </div>
  )
}
