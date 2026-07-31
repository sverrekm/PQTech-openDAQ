import { Fragment, useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import {
  fetchWifiStatus,
  skannWifi,
  kobleWifi,
  gloymWifi,
} from '../api/wifi'
import type { WifiStatus, WifiNett } from '../api/wifi'
import { useI18n } from '../i18n'

function signalBars(signal: number): string {
  if (signal >= 75) return '▁▃▅▇'
  if (signal >= 50) return '▁▃▅'
  if (signal >= 25) return '▁▃'
  return '▁'
}

export default function WifiCard() {
  const { t } = useI18n()
  const [nett, setNett] = useState<WifiNett[] | null>(null)
  const [valt, setValt] = useState<string>('')
  const [passord, setPassord] = useState('')
  const [skjultSsid, setSkjultSsid] = useState('')
  const [laddar, setLaddar] = useState(false)
  const [skannar, setSkannar] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [feil, setFeil] = useState<string | null>(null)

  const statusFetcher = useCallback(() => fetchWifiStatus(), [])
  const { data: status, refresh } = usePolling<WifiStatus>(statusFetcher, 8000)

  const handleSkann = async () => {
    setSkannar(true); setFeil(null); setMelding(null)
    try {
      const res = await skannWifi()
      if (res.suksess) setNett(res.nett || [])
      else setFeil(res.melding || t('Scan failed'))
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setSkannar(false)
    }
  }

  const handleKoble = async (ssid: string, open: boolean) => {
    if (!open && !passord.trim()) {
      setFeil(t('Enter the WiFi password'))
      return
    }
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await kobleWifi({ ssid, passord: open ? undefined : passord.trim() })
      if (res.suksess) { setMelding(res.melding); setPassord(''); setValt('') }
      else setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const handleSkjult = async () => {
    if (!skjultSsid.trim()) { setFeil(t('Enter the network name (SSID)')); return }
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await kobleWifi({ ssid: skjultSsid.trim(), passord: passord.trim() || undefined, skjult: true })
      if (res.suksess) { setMelding(res.melding); setPassord(''); setSkjultSsid('') }
      else setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const handleGloym = async () => {
    if (!status?.ssid) return
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await gloymWifi(status.ssid)
      if (res.suksess) setMelding(res.melding)
      else setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const statusFarge = () => {
    if (!status || !status.nmcli_tilgjengeleg) return 'text-gray-400'
    return status.tilkobla ? 'text-green-500' : 'text-gray-400'
  }
  const statusTekst = () => {
    if (!status) return t('Loading...')
    if (!status.nmcli_tilgjengeleg) return t('Not available')
    if (status.tilkobla) return t('Connected')
    return t('Not connected')
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900">{t('WiFi')}</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {t('Connect the device to a wireless network')}
          </p>
        </div>
        <span className={`text-sm font-medium ${statusFarge()}`}>{statusTekst()}</span>
      </div>

      {status && !status.nmcli_tilgjengeleg && (
        <div className="p-2 mb-3 bg-yellow-50 border border-yellow-200 rounded text-sm text-yellow-700">
          {status.feil || t('NetworkManager (nmcli) not found on the host. Requires Raspberry Pi OS Bookworm or newer.')}
        </div>
      )}

      {status?.tilkobla && (
        <div className="p-3 bg-gray-50 rounded-lg mb-3 space-y-1">
          <div className="flex justify-between text-sm">
            <span className="text-gray-500">{t('Network')}</span>
            <span className="font-medium text-gray-900">{status.ssid}</span>
          </div>
          {status.ip && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">{t('IP')}</span>
              <span className="font-mono text-gray-900">{status.ip}</span>
            </div>
          )}
          {status.signal != null && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">{t('Signal')}</span>
              <span className="text-gray-900">{signalBars(status.signal)} {status.signal}%</span>
            </div>
          )}
        </div>
      )}

      {feil && (
        <div className="p-2 mb-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{feil}</div>
      )}
      {melding && (
        <div className="p-2 mb-3 bg-green-50 border border-green-200 rounded text-sm text-green-700">{melding}</div>
      )}

      {status?.nmcli_tilgjengeleg && (
        <>
          <div className="flex gap-2 mb-3">
            <button
              onClick={handleSkann}
              disabled={skannar}
              className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50"
            >
              {skannar ? t('Scanning...') : t('Scan for networks')}
            </button>
            {status?.tilkobla && (
              <button
                onClick={handleGloym}
                disabled={laddar}
                className="px-3 py-1.5 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
              >
                {t('Forget network')}
              </button>
            )}
          </div>

          {nett && nett.length > 0 && (
            <div className="border border-gray-200 rounded-lg overflow-hidden mb-3">
              <table className="w-full text-sm">
                <tbody>
                  {nett.map((n) => (
                    <Fragment key={n.ssid}>
                      <tr
                        className="border-t border-gray-100 first:border-t-0 cursor-pointer hover:bg-gray-50"
                        onClick={() => { setValt(valt === n.ssid ? '' : n.ssid); setPassord(''); setFeil(null) }}
                      >
                        <td className="px-3 py-2 text-gray-900">
                          {n.ssid} {n.aktiv && <span className="text-green-600 text-xs">({t('active')})</span>}
                        </td>
                        <td className="px-3 py-2 text-gray-500 text-xs">
                          {n.open ? t('Open') : n.sikring || '🔒'}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-500 font-mono text-xs">
                          {signalBars(n.signal)} {n.signal}%
                        </td>
                      </tr>
                      {valt === n.ssid && (
                        <tr key={n.ssid + '-form'} className="bg-gray-50">
                          <td colSpan={3} className="px-3 py-2">
                            <div className="flex gap-2 items-end">
                              {!n.open && (
                                <div className="flex-1">
                                  <label className="block text-xs font-medium text-gray-600 mb-1">{t('Password')}</label>
                                  <input
                                    type="password"
                                    value={passord}
                                    onChange={(e) => setPassord(e.target.value)}
                                    className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
                                    autoFocus
                                  />
                                </div>
                              )}
                              <button
                                onClick={(e) => { e.stopPropagation(); handleKoble(n.ssid, n.open) }}
                                disabled={laddar}
                                className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50"
                              >
                                {laddar ? t('Connecting...') : t('Connect')}
                              </button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {nett && nett.length === 0 && (
            <p className="text-sm text-gray-500 mb-3">{t('No networks found.')}</p>
          )}

          {/* Skjult nett */}
          <details className="text-sm">
            <summary className="cursor-pointer text-gray-600 hover:text-gray-900">{t('Hidden network')}</summary>
            <div className="mt-2 space-y-2">
              <input
                type="text"
                value={skjultSsid}
                onChange={(e) => setSkjultSsid(e.target.value)}
                placeholder={t('Network name (SSID)')}
                className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
              />
              <input
                type="password"
                value={passord}
                onChange={(e) => setPassord(e.target.value)}
                placeholder={t('Password')}
                className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
              />
              <button
                onClick={handleSkjult}
                disabled={laddar}
                className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50"
              >
                {laddar ? t('Connecting...') : t('Connect')}
              </button>
            </div>
          </details>
        </>
      )}
    </div>
  )
}
