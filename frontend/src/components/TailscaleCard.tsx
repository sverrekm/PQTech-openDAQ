import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import {
  fetchTailscaleStatus,
  startTailscale,
  stoppTailscale,
  installerTailscale,
  avinstallerTailscale,
} from '../api/tailscale'
import type { TailscaleStatus } from '../api/types'
import { useI18n } from '../i18n'

export default function TailscaleCard() {
  const { t } = useI18n()
  const [authkey, setAuthkey] = useState('')
  const [hostname, setHostname] = useState('')
  const [laddar, setLaddar] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [feil, setFeil] = useState<string | null>(null)

  const statusFetcher = useCallback(() => fetchTailscaleStatus(), [])
  const { data: status, refresh } = usePolling<TailscaleStatus>(statusFetcher, 5000)

  const handleStart = async () => {
    if (!authkey.trim()) {
      setFeil(t('Auth key missing'))
      return
    }
    setLaddar(true)
    setFeil(null)
    setMelding(null)
    try {
      const res = await startTailscale({
        authkey: authkey.trim(),
        hostname: hostname.trim() || undefined,
      })
      if (res.suksess) {
        setMelding(res.melding)
        setAuthkey('')
      } else {
        setFeil(res.melding)
      }
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const handleInstaller = async () => {
    setLaddar(true)
    setFeil(null)
    setMelding(null)
    try {
      const res = await installerTailscale()
      if (res.suksess) {
        setMelding(res.melding)
      } else {
        setFeil(res.melding)
      }
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const handleAvinstaller = async () => {
    setLaddar(true)
    setFeil(null)
    setMelding(null)
    try {
      const res = await avinstallerTailscale()
      if (res.suksess) {
        setMelding(res.melding)
      } else {
        setFeil(res.melding)
      }
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const handleStopp = async () => {
    setLaddar(true)
    setFeil(null)
    setMelding(null)
    try {
      const res = await stoppTailscale()
      if (res.suksess) {
        setMelding(res.melding)
      } else {
        setFeil(res.melding)
      }
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const statusFarge = () => {
    if (!status) return 'text-gray-400'
    if (status.tilkobla) return 'text-green-500'
    if (status.daemon_køyrer) return 'text-yellow-500'
    return 'text-gray-400'
  }

  const statusTekst = () => {
    if (!status) return t('Loading...')
    if (!status.installert) return t('Not installed')
    if (status.tilkobla) return t('Connected')
    if (status.daemon_køyrer) return t('Daemon running, not connected')
    return t('Off')
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900">{t('Tailscale VPN')}</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {t('Encrypted mesh VPN for access between nodes')}
          </p>
        </div>
        <span className={`text-sm font-medium ${statusFarge()}`}>
          {statusTekst()}
        </span>
      </div>

      {status?.tilkobla && (
        <div className="p-3 bg-gray-50 rounded-lg mb-3 space-y-1">
          {status.ip && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">{t('Tailscale IP')}</span>
              <span className="font-mono text-gray-900">{status.ip}</span>
            </div>
          )}
          {status.hostname && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">{t('Hostname')}</span>
              <span className="text-gray-900">{status.hostname}</span>
            </div>
          )}
          {status.tailnet && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">{t('Tailnet')}</span>
              <span className="text-gray-900">{status.tailnet}</span>
            </div>
          )}
        </div>
      )}

      {status?.tilkobla && status.nodar.length > 0 && (
        <div className="mb-3">
          <h3 className="text-sm font-medium text-gray-700 mb-1">{t('Nodes in tailnet')}</h3>
          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-3 py-1.5 text-xs text-gray-500 font-medium">{t('Hostname')}</th>
                  <th className="text-left px-3 py-1.5 text-xs text-gray-500 font-medium">{t('IP')}</th>
                  <th className="text-center px-3 py-1.5 text-xs text-gray-500 font-medium">{t('Status')}</th>
                </tr>
              </thead>
              <tbody>
                {status.nodar.map((node, i) => (
                  <tr key={i} className="border-t border-gray-100">
                    <td className="px-3 py-1.5 text-gray-900">{node.hostname}</td>
                    <td className="px-3 py-1.5 font-mono text-gray-600">{node.ip}</td>
                    <td className="px-3 py-1.5 text-center">
                      <span
                        className={`inline-block w-2 h-2 rounded-full ${
                          node.online ? 'bg-green-500' : 'bg-gray-300'
                        }`}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!status?.tilkobla && status?.installert && (
        <div className="space-y-2 mb-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('Auth Key')}</label>
            <input
              type="password"
              value={authkey}
              onChange={(e) => { setAuthkey(e.target.value); setFeil(null) }}
              placeholder="tskey-auth-..."
              className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              {t('Hostname')} <span className="text-gray-400">{t('(optional)')}</span>
            </label>
            <input
              type="text"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="e.g. pi-workshop"
              className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
            />
          </div>
        </div>
      )}

      {feil && (
        <div className="p-2 mb-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {feil}
        </div>
      )}
      {melding && (
        <div className="p-2 mb-3 bg-green-50 border border-green-200 rounded text-sm text-green-700">
          {melding}
        </div>
      )}
      {status?.feil && !feil && (
        <div className="p-2 mb-3 bg-yellow-50 border border-yellow-200 rounded text-sm text-yellow-700">
          {status.feil}
        </div>
      )}

      <div className="flex gap-2">
        {status && !status.installert && (
          <button
            onClick={handleInstaller}
            disabled={laddar}
            className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50"
          >
            {laddar ? t('Installing...') : t('Install Tailscale')}
          </button>
        )}
        {status?.installert && !status.tilkobla && (
          <button
            onClick={handleStart}
            disabled={laddar}
            className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50"
          >
            {laddar ? t('Starting...') : t('Start Tailscale')}
          </button>
        )}
        {status?.installert && status.tilkobla && (
          <button
            onClick={handleStopp}
            disabled={laddar}
            className="px-3 py-1.5 text-sm bg-gray-500 text-white rounded hover:bg-gray-600 disabled:opacity-50"
          >
            {laddar ? t('Stopping...') : t('Stop Tailscale')}
          </button>
        )}
        {status?.installert && !status.tilkobla && (
          <button
            onClick={handleAvinstaller}
            disabled={laddar}
            className="px-3 py-1.5 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
          >
            {laddar ? t('Uninstalling...') : t('Uninstall')}
          </button>
        )}
      </div>
    </div>
  )
}
