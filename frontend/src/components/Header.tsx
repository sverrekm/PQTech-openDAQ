import { loggUt } from '../api/auth'
import StatusBadge from './StatusBadge'
import { useI18n } from '../i18n'

interface Props {
  serverOk: boolean
  loading: boolean
  onLogout?: () => void
  disconnected?: boolean
}

export default function Header({ serverOk, loading, onLogout, disconnected }: Props) {
  const { t } = useI18n()

  const handleLogout = async () => {
    await loggUt()
    onLogout?.()
  }

  return (
    <>
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
              {t('Log out')}
            </button>
          )}
        </div>
      </div>
      {disconnected && (
        <div className="bg-amber-500 text-amber-950 text-sm font-medium px-4 md:px-6 py-2 flex items-center gap-2">
          <svg className="animate-spin h-4 w-4 flex-shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          {t('Lost contact with the measurement box — trying to reconnect...')}
        </div>
      )}
    </>
  )
}
