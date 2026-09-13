import { loggUt } from '../api/auth'
import { useI18n } from '../i18n'

interface Props {
  serverOk: boolean
  loading: boolean
  onLogout?: () => void
  disconnected?: boolean
  onMenu?: () => void
  enhetsnamn?: string
  ip?: string
  modus?: string
}

/** Eit info-felt i headeren (mono-etikett over verdi), blueprint-stil. */
function InfoBlokk({ label, verdi }: { label: string; verdi: string }) {
  return (
    <div className="hidden lg:flex flex-col justify-center px-4 border-l border-white/10">
      <span className="mono text-[9px] tracking-[0.14em] uppercase text-white/40">{label}</span>
      <span className="mono text-[13px] text-white/90 leading-tight max-w-[180px] truncate">{verdi}</span>
    </div>
  )
}

export default function Header({ serverOk, loading, onLogout, disconnected, onMenu, enhetsnamn, ip, modus }: Props) {
  const { t } = useI18n()

  const handleLogout = async () => {
    await loggUt()
    onLogout?.()
  }

  return (
    <>
      <div
        className="bg-[#1a1a1a] border-b-[3px] border-b-[#D76428] px-4 md:px-6 flex items-stretch justify-between"
        style={{ minHeight: 66 }}
      >
        <div className="flex items-stretch gap-1">
          <div className="flex items-center gap-3">
            {onMenu && (
              <button
                onClick={onMenu}
                aria-label="Meny"
                className="md:hidden text-gray-300 hover:text-white p-1 -ml-1"
              >
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
            )}
            <span
              className="text-white pr-1"
              style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 22, letterSpacing: '0.02em' }}
            >
              <span className="text-[#D76428]">PQTECH</span> openDAQ
            </span>
          </div>
          {enhetsnamn && <InfoBlokk label={t('Node')} verdi={enhetsnamn} />}
          {ip && <InfoBlokk label={t('Address')} verdi={ip} />}
          {modus && <InfoBlokk label={t('Mode')} verdi={modus} />}
        </div>

        <div className="flex items-center gap-4">
          <div className="hidden sm:flex items-center gap-2">
            <span
              className="w-[7px] h-[7px]"
              style={{ background: serverOk ? '#D76428' : '#ef4444' }}
            />
            <span className="mono text-[11px] tracking-[0.1em] uppercase text-white">
              {loading ? t('Connecting…') : serverOk ? t('Server active') : t('Server down')}
            </span>
          </div>
          {onLogout && (
            <button
              onClick={handleLogout}
              className="mono text-[11px] tracking-[0.08em] uppercase text-gray-400 hover:text-white border border-gray-600 hover:border-gray-400 px-3 py-1.5 transition-colors"
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
