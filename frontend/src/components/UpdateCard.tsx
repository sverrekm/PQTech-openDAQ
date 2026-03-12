import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchVersjon, sjekkOppdatering, utfoerOppdatering } from '../api/system'
import type { OppdateringSjekk } from '../api/types'
import InfoGrid from './InfoGrid'
import { useI18n } from '../i18n'

export default function UpdateCard() {
  const { t, lang } = useI18n()
  const fetcher = useCallback(() => fetchVersjon(), [])
  const { data: versjon, loading } = usePolling(fetcher, 30000)
  const [sjekk, setSjekk] = useState<OppdateringSjekk | null>(null)
  const [busy, setBusy] = useState(false)
  const [restartar, setRestartar] = useState(false)
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  const locale = lang === 'nb' ? 'nb-NO' : 'en-US'

  const handleSjekk = async () => {
    setBusy(true)
    setMelding(null)
    try {
      const res = await sjekkOppdatering()
      if (res.feil) {
        setMelding({ text: res.feil, ok: false })
      } else {
        setSjekk(res)
        if (!res.oppdatering_tilgjengeleg) {
          setMelding({ text: t('Already up to date'), ok: true })
        }
      }
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
    setBusy(false)
  }

  const handleOppdater = async () => {
    if (!confirm(t('Are you sure you want to update? The container will restart.'))) return
    setBusy(true)
    setMelding(null)
    try {
      const res = await utfoerOppdatering()
      if (res.suksess) {
        setMelding({ text: `${res.versjon} — ${res.oppdaterte_filer?.length ?? 0} ${t('files')}. ${t('Restart')}...`, ok: true })
        setRestartar(true)
      } else {
        setMelding({ text: res.feil || t('Update failed'), ok: false })
      }
    } catch (e) {
      setMelding({ text: String(e), ok: false })
    }
    setBusy(false)
  }

  if (loading) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm flex justify-center items-center h-32">
        <svg className="animate-spin h-8 w-8 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

  if (restartar) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3 shadow-sm">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">{t('Update')}</h2>
        <div className="flex items-center gap-3 py-4">
          <svg className="animate-spin h-6 w-6 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span className="text-sm text-gray-700">{t('The container is restarting with updated files...')}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3 shadow-sm">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold">{t('Update')}</h2>
        {sjekk && (
          <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
            sjekk.oppdatering_tilgjengeleg
              ? 'bg-orange-100 text-orange-800'
              : 'bg-green-100 text-green-800'
          }`}>
            {sjekk.oppdatering_tilgjengeleg ? t('New version available') : t('Up to date')}
          </span>
        )}
      </div>

      <InfoGrid items={[
        { label: t('Version'), value: versjon?.sha || t('unknown') },
        { label: t('Latest commit'), value: versjon?.melding || '-' },
        { label: t('Date'), value: versjon?.dato ? new Date(versjon.dato).toLocaleString(locale) : '-' },
      ]} />

      {sjekk?.oppdatering_tilgjengeleg && sjekk.github && (
        <div className="mt-3 p-3 bg-orange-50 border border-orange-200 rounded-lg text-sm">
          <div className="font-medium text-orange-800 mb-1">{t('New version on GitHub:')}</div>
          <div className="text-orange-700">
            <span className="font-mono">{sjekk.github.sha}</span> — {sjekk.github.melding}
          </div>
          <div className="text-orange-600 text-xs mt-1">
            {sjekk.github.forfattar} · {new Date(sjekk.github.dato).toLocaleString(locale)}
          </div>
        </div>
      )}

      <div className="flex gap-2 mt-3">
        <button
          className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={busy}
          onClick={handleSjekk}
        >
          {t('Check for update')}
        </button>
        {sjekk?.oppdatering_tilgjengeleg && (
          <button
            className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={busy}
            onClick={handleOppdater}
          >
            {t('Update now')}
          </button>
        )}
      </div>

      {melding && (
        <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${melding.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
          {melding.text}
        </div>
      )}
    </div>
  )
}
