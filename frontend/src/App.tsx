import { useState, useCallback } from 'react'
import { fetchStatus } from './api/status'
import { usePolling } from './hooks/usePolling'
import type { ServerStatus } from './api/types'
import Header from './components/Header'
import TabBar from './components/TabBar'
import DashboardPage from './pages/DashboardPage'
import SettingsPage from './pages/SettingsPage'

type Tab = 'dashboard' | 'settings'

export default function App() {
  const [tab, setTab] = useState<Tab>('dashboard')
  const fetcher = useCallback(() => fetchStatus(), [])
  const { data: status } = usePolling(fetcher, 5000)

  return (
    <>
      <Header serverOk={status?.server_kjorer ?? false} />
      <div className="main">
        <TabBar active={tab} onChange={setTab} />
        {tab === 'dashboard'
          ? <DashboardPage status={status} />
          : <SettingsPage status={status} />}
      </div>
    </>
  )
}
