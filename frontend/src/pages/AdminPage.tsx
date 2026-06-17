import { useState } from 'react'
import { endrePassord } from '../api/auth'
import { restartSystem } from '../api/system'
import { useI18n } from '../i18n'
import UpdateCard from '../components/UpdateCard'

export default function AdminPage() {
  const { t, lang, setLang } = useI18n()

  // Restart state
  const [restartBusy, setRestartBusy] = useState(false)
  const [restartDone, setRestartDone] = useState(false)

  const handleRestart = async () => {
    if (!confirm(t('Are you sure you want to restart the system?'))) return
    setRestartBusy(true)
    try {
      await restartSystem()
      setRestartDone(true)
    } catch {
      // Forventa — serveren avsluttar
      setRestartDone(true)
    }
  }

  // Password change state
  const [gammalt, setGammalt] = useState('')
  const [nytt, setNytt] = useState('')
  const [stadfest, setStadfest] = useState('')
  const [pwMelding, setPwMelding] = useState<{ text: string; ok: boolean } | null>(null)
  const [pwBusy, setPwBusy] = useState(false)

  const handleEndrePassord = async (e: React.FormEvent) => {
    e.preventDefault()
    if (nytt !== stadfest) {
      setPwMelding({ text: t('Passwords do not match'), ok: false })
      return
    }
    setPwBusy(true)
    setPwMelding(null)
    try {
      const res = await endrePassord(gammalt, nytt)
      setPwMelding({ text: res.melding || '', ok: res.suksess })
      if (res.suksess) {
        setGammalt('')
        setNytt('')
        setStadfest('')
      }
    } catch (err) {
      setPwMelding({ text: String(err), ok: false })
    }
    setPwBusy(false)
  }

  return (
    <>
      {/* Change password */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">{t('Change password')}</h2>
        <form onSubmit={handleEndrePassord} className="space-y-3 max-w-sm">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('Current password')}</label>
            <input
              type="password"
              value={gammalt}
              onChange={e => setGammalt(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('New password')}</label>
            <input
              type="password"
              value={nytt}
              onChange={e => setNytt(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('Confirm password')}</label>
            <input
              type="password"
              value={stadfest}
              onChange={e => setStadfest(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
            />
          </div>
          <button
            type="submit"
            disabled={pwBusy || !gammalt || !nytt || !stadfest}
            className="px-4 py-2 bg-[#D76428] text-white text-sm font-medium rounded-md hover:bg-[#c0571f] disabled:opacity-50 transition-colors"
          >
            {pwBusy ? t('Changing...') : t('Change password')}
          </button>
          {pwMelding && (
            <div className={`px-3 py-2 rounded-lg text-sm ${pwMelding.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
              {pwMelding.text}
            </div>
          )}
        </form>
      </div>

      {/* Language selector */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3">{t('Language')}</h2>
        <select
          value={lang}
          onChange={e => setLang(e.target.value as 'en' | 'nb')}
          className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
        >
          <option value="en">English</option>
          <option value="nb">Norsk (Bokmål)</option>
        </select>
      </div>

      {/* Support bundle */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3">{t('Support')}</h2>
        <p className="text-sm text-gray-500 mb-3">{t('Download logs and config to send to PQ Tech support. Secrets are removed.')}</p>
        <a
          href="/api/system/support-bundle"
          className="inline-block px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-800 text-sm font-medium rounded-md transition-colors"
        >
          {t('Download support bundle')}
        </a>
      </div>

      {/* Restart */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3">{t('System')}</h2>
        {restartDone ? (
          <div className="bg-blue-50 border border-blue-200 text-blue-800 text-sm rounded-lg p-3">
            {t('System is restarting. Please wait and refresh the page.')}
          </div>
        ) : (
          <button
            onClick={handleRestart}
            disabled={restartBusy}
            className="px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-md hover:bg-red-700 disabled:opacity-50 transition-colors"
          >
            {restartBusy ? t('Restarting...') : t('Restart system')}
          </button>
        )}
      </div>

      {/* Update (moved from Dashboard) */}
      <UpdateCard />
    </>
  )
}
