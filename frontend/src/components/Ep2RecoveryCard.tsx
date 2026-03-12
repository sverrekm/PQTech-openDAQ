import { useState, useCallback } from 'react'
import { fetchSiriusStatus, siriusGjenopplivEp2 } from '../api/sirius'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'

export default function Ep2RecoveryCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchSiriusStatus(), [])
  const { data: s, refresh, loading } = usePolling(fetcher, 5000)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  if (loading) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm flex justify-center items-center h-48">
        <svg className="animate-spin h-8 w-8 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

  if (!s || !s.tilgjengelig || !s.tilkoblet || s.ep2_ok !== false) return null

  const handle = async () => {
    setBusy(true)
    try {
      const res = await siriusGjenopplivEp2()
      setMelding({ text: res.melding, ok: res.suksess })
      refresh()
    } catch {
      setMelding({ text: t('Network error'), ok: false })
    }
    setBusy(false)
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">{t('EP2 Recovery')}</h2>
      <p className="text-red-700 text-sm mb-3">
        {t('EP2 (data endpoint) is down. Try to recover with different strategies.')}
      </p>
      <button className="bg-yellow-100 hover:bg-yellow-200 text-yellow-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={busy} onClick={handle}>
        {busy ? t('Trying strategies...') : t('Recover EP2')}
      </button>
      {melding && (
        <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${melding.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
          {melding.text}
        </div>
      )}
    </div>
  )
}
