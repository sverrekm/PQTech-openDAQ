import { useState, useCallback } from 'react'
import { fetchUsbIpStatus, usbipDel, usbipStopp } from '../api/usbip'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'
import CopyableCommand from './CopyableCommand'
import { useI18n } from '../i18n'

interface Props {
  ip: string
}

export default function UsbIpCard({ ip }: Props) {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchUsbIpStatus(), [])
  const { data: u, refresh, loading } = usePolling(fetcher, 5000)
  const [feil, setFeil] = useState<string | null>(null)
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

  if (!u) return null

  const handleDel = async () => {
    setBusy(true)
    setFeil(null)
    try {
      const res = await usbipDel()
      if (!res.suksess) setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil(t('Network error: ') + String(e))
    }
    setBusy(false)
  }

  const handleStopp = async () => {
    setBusy(true)
    setFeil(null)
    try {
      await usbipStopp()
      refresh()
    } catch (e) {
      setFeil(t('Network error: ') + String(e))
    }
    setBusy(false)
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">{t('USB/IP — Share instrument')}</h2>
      <div className="flex items-center gap-2 mb-3 text-sm">
        <span className={`w-2 h-2 rounded-full ${u.deling_aktiv ? 'bg-green-500' : 'bg-red-500'}`} />
        <span>
          {u.deling_aktiv
            ? t('Sharing active on port 3240')
            : u.tilgjengelig ? t('Ready') : t('USB/IP unavailable')}
        </span>
      </div>
      <InfoGrid items={[
        {
          label: t('Instrument on USB'),
          value: u.sirius_paa_usb ? (u.sirius_enhet_funnet || t('Yes')) : t('No'),
          color: u.sirius_paa_usb ? '#10b981' : '#ef4444',
        },
        { label: t('Bus ID'), value: u.busid || u.sirius_busid_funnet || '-' },
        {
          label: t('Sharing'),
          value: u.deling_aktiv ? t('Active') : t('Inactive'),
          color: u.deling_aktiv ? '#10b981' : '#6b6b6b',
        },
      ]} />
      {(feil || u.feil) && (
        <div className="mt-3 px-3 py-2 rounded-lg text-sm bg-red-100 text-red-800">
          {feil || u.feil}
        </div>
      )}
      <div className="flex flex-wrap gap-2 mt-3">
        {!u.deling_aktiv && (
          <button
            className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={busy || !u.sirius_paa_usb || !u.tilgjengelig}
            onClick={handleDel}
          >
            {t('Share via USB/IP')}
          </button>
        )}
        {u.deling_aktiv && (
          <button className="bg-red-100 hover:bg-red-200 text-red-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={busy} onClick={handleStopp}>
            {t('Stop sharing')}
          </button>
        )}
      </div>
      {u.deling_aktiv && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 mt-4">
          <h3 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3">{t('On Windows PC')}</h3>
          <ol className="list-decimal pl-5 text-sm text-gray-700">
            <li>
              {t('Install')}{' '}
              <a href="https://github.com/cezanne/usbip-win2/releases" target="_blank" rel="noreferrer" className="text-[#D76428] hover:underline">
                usbip-win2
              </a>
            </li>
            <li>{t('Open PowerShell as Administrator')}</li>
            <li>
              {t('List devices:')}
              <CopyableCommand text={`usbip list -r ${ip}`} className="mt-1" />
            </li>
            <li>
              {t('Connect instrument:')}
              <CopyableCommand
                text={`usbip attach -r ${ip} -b ${u.busid || 'X-Y'}`}
                className="mt-1"
              />
            </li>
            <li>{t('Open DewesoftX — the instrument appears as a local USB device')}</li>
          </ol>
        </div>
      )}
    </div>
  )
}
