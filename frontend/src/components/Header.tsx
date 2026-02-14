import { useCallback } from 'react'
import StatusBadge from './StatusBadge'
import { fetchStatus } from '../api/status'
import { usePolling } from '../hooks/usePolling'

export default function Header() {
  const fetcher = useCallback(() => fetchStatus(), [])
  const { data } = usePolling(fetcher, 5000)

  return (
    <div className="header">
      <h1><span>PQTech</span>-openDAQ</h1>
      <StatusBadge ok={data?.server_kjorer ?? false} />
    </div>
  )
}
