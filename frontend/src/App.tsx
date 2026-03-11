import { useCallback, useEffect, useState } from 'react'
import { fetchStatus } from './api/status'
import { fetchKanalar, fetchKanalLive } from './api/kanalar'
import { fetchMqttStatus } from './api/mqtt'
import { fetchSiriusStatus } from './api/sirius'
import { sjekkAuth } from './api/auth'
import { apiGet } from './api/client'
import { usePolling } from './hooks/usePolling'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import type { View } from './components/Sidebar'
import DashboardPage from './pages/DashboardPage'
import SettingsPage from './pages/SettingsPage'
import ChannelPage from './pages/ChannelPage'
import HubPage from './pages/HubPage'
import LoginPage from './pages/LoginPage'
import Layout from './components/Layout'

export default function App() {
  const [innlogga, setInnlogga] = useState<boolean | null>(null)

  useEffect(() => {
    sjekkAuth()
      .then(res => setInnlogga(res.innlogga))
      .catch(() => setInnlogga(false))
  }, [])

  // Spinner while checking auth
  if (innlogga === null) {
    return (
      <div className="min-h-screen bg-[#111] flex items-center justify-center">
        <svg className="animate-spin h-8 w-8 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

  if (!innlogga) {
    return <LoginPage onLogin={() => setInnlogga(true)} />
  }

  return <AuthenticatedApp onLogout={() => setInnlogga(false)} />
}

function AuthenticatedApp({ onLogout }: { onLogout: () => void }) {
  const [modus, setModus] = useState<string | null>(null)
  const [view, setView] = useState<View>({ page: 'dashboard' })

  // Hent modus ved oppstart
  useEffect(() => {
    apiGet<{ modus: string }>('/api/modus')
      .then(res => {
        setModus(res.modus)
        if (res.modus === 'hub') {
          setView({ page: 'hub' })
        }
      })
      .catch(() => setModus('ukjend'))
  }, [])

  const isHub = modus === 'hub'

  const statusFetcher = useCallback(() => fetchStatus(), [])
  const { data: status, loading: statusLoading } = usePolling(statusFetcher, 5000)

  const liveFetcher = useCallback(() => isHub ? Promise.resolve(null) : fetchKanalLive(), [isHub])
  const { data: liveData, loading: liveDataLoading } = usePolling(liveFetcher, 2000)

  const kanalFetcher = useCallback(() => isHub ? Promise.resolve(null) : fetchKanalar(), [isHub])
  const { data: kanalar, loading: kanalarLoading } = usePolling(kanalFetcher, 5000)

  const mqttFetcher = useCallback(() => isHub ? Promise.resolve(null) : fetchMqttStatus(), [isHub])
  const { data: mqttStatus } = usePolling(mqttFetcher, 3000)

  const siriusFetcher = useCallback(() => isHub ? Promise.resolve(null) : fetchSiriusStatus(), [isHub])
  const { data: siriusStatus } = usePolling(siriusFetcher, 5000)

  const handleChannelClick = (index: number) => {
    setView({ page: 'channel', index })
  }

  let content: React.ReactNode
  const isLoading = !isHub && (statusLoading || liveDataLoading || kanalarLoading)

  if (modus === null) {
    content = (
      <div className="flex justify-center items-center h-full">
        <div className="flex items-center space-x-2 text-gray-500">
          <svg className="animate-spin h-5 w-5 mr-3 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          Laster data...
        </div>
      </div>
    )
  } else if (isLoading) {
    content = (
      <div className="flex justify-center items-center h-full">
        <div className="flex items-center space-x-2 text-gray-500">
          <svg className="animate-spin h-5 w-5 mr-3 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          Laster data...
        </div>
      </div>
    )
  } else if (view.page === 'hub') {
    content = <HubPage />
  } else if (view.page === 'dashboard') {
    content = <DashboardPage status={status} kanalar={kanalar} liveData={liveData} mqttStatus={mqttStatus} siriusTilkoblet={siriusStatus?.tilkoblet ?? false} onChannelClick={handleChannelClick} />
  } else if (view.page === 'settings') {
    content = <SettingsPage />
  } else {
    content = <ChannelPage index={view.index} kanalar={kanalar ?? []} liveData={liveData} onBack={() => setView({ page: isHub ? 'hub' : 'dashboard' })} />
  }

  return (
    <>
      <Header serverOk={status?.server_kjorer ?? false} loading={isLoading} onLogout={onLogout} />
      <Layout>
        <Sidebar view={view} onNavigate={setView} kanalar={kanalar} liveData={liveData} mqttStatus={mqttStatus} isHub={isHub} />
        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-4xl mx-auto">
            {content}
          </div>
        </div>
      </Layout>
    </>
  )
}
