import { useState } from 'react'
import Header from './components/Header'
import TabBar from './components/TabBar'
import DashboardPage from './pages/DashboardPage'
import SettingsPage from './pages/SettingsPage'

type Tab = 'dashboard' | 'settings'

export default function App() {
  const [tab, setTab] = useState<Tab>('dashboard')

  return (
    <>
      <Header />
      <div className="main">
        <TabBar active={tab} onChange={setTab} />
        {tab === 'dashboard' ? <DashboardPage /> : <SettingsPage />}
      </div>
    </>
  )
}
