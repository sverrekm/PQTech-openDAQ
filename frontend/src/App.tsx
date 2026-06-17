import { useCallback, useEffect, useState } from 'react'
import { fetchStatus } from './api/status'
import { fetchKanalar, fetchKanalLive } from './api/kanalar'
import { fetchMqttStatus } from './api/mqtt'
import { fetchSiriusStatus } from './api/sirius'
import { fetchHubKanalar, fetchHubStatus } from './api/hub'
import { sjekkAuth } from './api/auth'
import { usePolling } from './hooks/usePolling'
import { useI18n } from './i18n'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import type { View } from './components/Sidebar'
import DashboardPage from './pages/DashboardPage'
import SettingsPage from './pages/SettingsPage'
import ChannelPage from './pages/ChannelPage'
import HubChannelPage from './pages/HubChannelPage'
import MqttChannelPage from './pages/MqttChannelPage'
import HubPage from './pages/HubPage'
import AdminPage from './pages/AdminPage'
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
  const { t } = useI18n()
  const [view, setView] = useState<View>({ page: 'dashboard' })

  const statusFetcher = useCallback(() => fetchStatus(), [])
  const { data: status, loading: statusLoading } = usePolling(statusFetcher, 5000)

  const liveFetcher = useCallback(() => fetchKanalLive(), [])
  const { data: liveData, loading: liveDataLoading } = usePolling(liveFetcher, 2000)

  const kanalFetcher = useCallback(() => fetchKanalar(), [])
  const { data: kanalar, loading: kanalarLoading } = usePolling(kanalFetcher, 5000)

  const mqttFetcher = useCallback(() => fetchMqttStatus(), [])
  const { data: mqttStatus } = usePolling(mqttFetcher, 3000)

  const siriusFetcher = useCallback(() => fetchSiriusStatus(), [])
  const { data: siriusStatus } = usePolling(siriusFetcher, 5000)

  const hubKanalFetcher = useCallback(() => fetchHubKanalar(), [])
  const { data: hubKanalData } = usePolling(hubKanalFetcher, 3000)
  const hubKanalar = hubKanalData?.kanalar ?? []

  // Hub-mode detection
  const hubStatusFetcher = useCallback(() => fetchHubStatus(), [])
  const { data: hubStatusData } = usePolling(hubStatusFetcher, 5000)
  // modus='hub' vs 'node'. 'aktiv' vert sett òg i direkte-modus for
  // modbus-status, so vi må sjekke sjølve modus-feltet.
  const isHubMode = hubStatusData?.modus === 'hub'

  const handleChannelClick = (index: number) => {
    setView({ page: 'channel', index })
  }
  const handleMqttClick = (topic: string) => {
    setView({ page: 'mqttChannel', topic })
  }
  const handleHubClick = (nodeId: string, namn: string) => {
    setView({ page: 'hubChannel', nodeId, namn })
  }

  let content: React.ReactNode
  const isLoading = statusLoading || liveDataLoading || kanalarLoading

  if (isLoading) {
    content = (
      <div className="flex justify-center items-center h-full">
        <div className="flex items-center space-x-2 text-gray-500">
          <svg className="animate-spin h-5 w-5 mr-3 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          {t('Loading data...')}
        </div>
      </div>
    )
  } else if (view.page === 'hub') {
    content = <HubPage />
  } else if (view.page === 'dashboard') {
    content = <DashboardPage status={status} kanalar={kanalar} liveData={liveData} mqttStatus={mqttStatus} siriusTilkoblet={siriusStatus?.tilkoblet ?? false} onChannelClick={handleChannelClick} onMqttClick={handleMqttClick} onHubClick={handleHubClick} hubKanalar={hubKanalar} isHubMode={isHubMode} />
  } else if (view.page === 'settings') {
    content = <SettingsPage />
  } else if (view.page === 'admin') {
    content = <AdminPage />
  } else if (view.page === 'mqttChannel') {
    content = <MqttChannelPage topic={view.topic} mqttStatus={mqttStatus ?? null} onBack={() => setView({ page: 'dashboard' })} />
  } else if (view.page === 'hubChannel') {
    content = <HubChannelPage nodeId={view.nodeId} namn={view.namn} hubKanalar={hubKanalar} onBack={() => setView({ page: 'dashboard' })} />
  } else {
    content = <ChannelPage index={view.index} kanalar={kanalar ?? []} liveData={liveData} onBack={() => setView({ page: 'dashboard' })} />
  }

  return (
    <div className="h-screen flex flex-col">
      <Header serverOk={status?.server_kjorer ?? false} loading={isLoading} onLogout={onLogout} />
      <Layout>
        <Sidebar view={view} onNavigate={setView} kanalar={kanalar} liveData={liveData} mqttStatus={mqttStatus} hubKanalar={hubKanalar} />
        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-4xl mx-auto">
            {content}
          </div>
        </div>
      </Layout>
    </div>
  )
}
