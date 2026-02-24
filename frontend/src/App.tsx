import { useCallback } from 'react'
import { useState } from 'react'
import { fetchStatus } from './api/status'
import { fetchKanalar, fetchKanalLive } from './api/kanalar'
import { usePolling } from './hooks/usePolling'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import type { View } from './components/Sidebar'
import DashboardPage from './pages/DashboardPage'
import SettingsPage from './pages/SettingsPage'
import ChannelPage from './pages/ChannelPage'

export default function App() {
  const [view, setView] = useState<View>({ page: 'dashboard' })

  const statusFetcher = useCallback(() => fetchStatus(), [])
  const { data: status } = usePolling(statusFetcher, 5000)

  const liveFetcher = useCallback(() => fetchKanalLive(), [])
  const { data: liveData } = usePolling(liveFetcher, 2000)

  const kanalFetcher = useCallback(() => fetchKanalar(), [])
  const { data: kanalar } = usePolling(kanalFetcher, 5000)

  const handleChannelClick = (index: number) => {
    setView({ page: 'channel', index })
  }

  let content: React.ReactNode
  if (view.page === 'dashboard') {
    content = <DashboardPage status={status} kanalar={kanalar} liveData={liveData} onChannelClick={handleChannelClick} />
  } else if (view.page === 'settings') {
    content = <SettingsPage />
  } else {
    content = <ChannelPage index={view.index} kanalar={kanalar ?? []} liveData={liveData} onBack={() => setView({ page: 'dashboard' })} />
  }

  return (
    <>
      <Header serverOk={status?.server_kjorer ?? false} />
      <div className="app-layout">
        <Sidebar view={view} onNavigate={setView} kanalar={kanalar} liveData={liveData} />
        <div className="innhald">
          <div className="innhald-inner">
            {content}
          </div>
        </div>
      </div>
    </>
  )
}
