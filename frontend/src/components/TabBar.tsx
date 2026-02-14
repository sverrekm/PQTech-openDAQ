type Tab = 'dashboard' | 'settings'

interface Props {
  active: Tab
  onChange: (tab: Tab) => void
}

export default function TabBar({ active, onChange }: Props) {
  return (
    <div className="tab-bar">
      <button
        className={`tab-btn ${active === 'dashboard' ? 'active' : ''}`}
        onClick={() => onChange('dashboard')}
      >
        Dashboard
      </button>
      <button
        className={`tab-btn ${active === 'settings' ? 'active' : ''}`}
        onClick={() => onChange('settings')}
      >
        Settings
      </button>
    </div>
  )
}
