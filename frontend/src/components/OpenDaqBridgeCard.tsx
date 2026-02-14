import { useState, useCallback } from 'react'
import { fetchOpenDaqStatus, restartOpenDaq } from '../api/opendaq'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'
import CopyableCommand from './CopyableCommand'

export default function OpenDaqBridgeCard() {
  const fetcher = useCallback(() => fetchOpenDaqStatus(), [])
  const { data: s, refresh } = usePolling(fetcher, 5000)
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

  return (
    <div className="kort">
      <h2>openDAQ Nettverksservere</h2>
      <InfoGrid items={[
        {
          label: 'Status',
          value: s.aktiv ? 'Aktiv' : 'Inaktiv',
          color: s.aktiv ? '#10b981' : '#ef4444',
        },
        { label: 'Enhet', value: s.enhet_namn || '-' },
        { label: 'Kanalar', value: s.kanalar?.length || '-' },
        { label: 'Servere', value: s.servere?.length || '-' },
      ]} />
      {s.feil && !s.aktiv && (
        <div className="melding melding-feil">{s.feil}</div>
      )}
      {s.aktiv && s.ip && (
        <>
          <InfoGrid items={[
            { label: 'OPC-UA', value: `${s.ip}:${s.porter?.opcua || 4840}`, small: true },
            { label: 'Native Streaming', value: `${s.ip}:${s.porter?.native_streaming || 7420}`, small: true },
            { label: 'WebSocket', value: `${s.ip}:${s.porter?.websocket || 7414}`, small: true },
          ]} />
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
