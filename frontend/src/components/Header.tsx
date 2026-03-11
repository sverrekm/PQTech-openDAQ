import { loggUt } from '../api/auth'
import StatusBadge from './StatusBadge'

interface Props {
  serverOk: boolean
  loading: boolean
  onLogout?: () => void
}

export default function Header({ serverOk, loading, onLogout }: Props) {
  const handleLogout = async () => {
    await loggUt()
    onLogout?.()
  }

  return (
    <div className="bg-[#1a1a1a] border-b-[3px] border-b-[#D76428] p-4 md:px-6 flex items-center justify-between">
      <h1 className="text-xl font-semibold text-white">
        <span className="text-[#D76428]">PQTech</span>-openDAQ
      </h1>
      <div className="flex items-center gap-3">
        <StatusBadge ok={serverOk} loading={loading} />
        {onLogout && (
          <button
            onClick={handleLogout}
            className="text-sm text-gray-400 hover:text-white border border-gray-600 hover:border-gray-400 rounded px-3 py-1 transition-colors"
          >
            Logg ut
          </button>
        )}
      </div>
    </div>
  )
}
