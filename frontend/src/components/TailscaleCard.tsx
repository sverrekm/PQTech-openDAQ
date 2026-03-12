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

export default function TailscaleCard() {
  const [authkey, setAuthkey] = useState('')
  const [hostname, setHostname] = useState('')
  const [laddar, setLaddar] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [feil, setFeil] = useState<string | null>(null)

  const statusFetcher = useCallback(() => fetchTailscaleStatus(), [])
  const { data: status, refresh } = usePolling<TailscaleStatus>(statusFetcher, 5000)

  const handleStart = async () => {
    if (!authkey.trim()) {
      setFeil('Auth key manglar')
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
    if (!status) return 'Lastar...'
    if (!status.installert) return 'Ikkje installert'
    if (status.tilkobla) return 'Tilkobla'
    if (status.daemon_køyrer) return 'Daemon køyrer, ikkje tilkobla'
    return 'Av'
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900">Tailscale VPN</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Kryptert mesh-VPN for tilgang mellom nodar
          </p>
        </div>
        <span className={`text-sm font-medium ${statusFarge()}`}>
          {statusTekst()}
        </span>
      </div>

      {/* Status-info */}
      {status?.tilkobla && (
        <div className="p-3 bg-gray-50 rounded-lg mb-3 space-y-1">
          {status.ip && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Tailscale IP</span>
              <span className="font-mono text-gray-900">{status.ip}</span>
            </div>
          )}
          {status.hostname && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Hostname</span>
              <span className="text-gray-900">{status.hostname}</span>
            </div>
          )}
          {status.tailnet && (
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Tailnet</span>
              <span className="text-gray-900">{status.tailnet}</span>
            </div>
          )}
        </div>
      )}

      {/* Node-liste */}
      {status?.tilkobla && status.nodar.length > 0 && (
        <div className="mb-3">
          <h3 className="text-sm font-medium text-gray-700 mb-1">Nodar i tailnet</h3>
          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-3 py-1.5 text-xs text-gray-500 font-medium">Hostname</th>
                  <th className="text-left px-3 py-1.5 text-xs text-gray-500 font-medium">IP</th>
                  <th className="text-center px-3 py-1.5 text-xs text-gray-500 font-medium">Status</th>
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

      {/* Start-skjema */}
      {!status?.tilkobla && status?.installert && (
        <div className="space-y-2 mb-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Auth Key</label>
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
              Hostname <span className="text-gray-400">(valfritt)</span>
            </label>
            <input
              type="text"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="t.d. pi-sundet"
              className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
            />
          </div>
        </div>
      )}

      {/* Feilmelding */}
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

      {/* Knappar */}
      <div className="flex gap-2">
        {status && !status.installert && (
          <button
            onClick={handleInstaller}
            disabled={laddar}
            className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50"
          >
            {laddar ? 'Installerer...' : 'Installer Tailscale'}
          </button>
        )}
        {status?.installert && !status.tilkobla && (
          <button
            onClick={handleStart}
            disabled={laddar}
            className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50"
          >
            {laddar ? 'Startar...' : 'Start Tailscale'}
          </button>
        )}
        {status?.installert && status.tilkobla && (
          <button
            onClick={handleStopp}
            disabled={laddar}
            className="px-3 py-1.5 text-sm bg-gray-500 text-white rounded hover:bg-gray-600 disabled:opacity-50"
          >
            {laddar ? 'Stoppar...' : 'Stopp Tailscale'}
          </button>
        )}
        {status?.installert && !status.tilkobla && (
          <button
            onClick={handleAvinstaller}
            disabled={laddar}
            className="px-3 py-1.5 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
          >
            {laddar ? 'Avinstallerer...' : 'Avinstaller'}
          </button>
        )}
      </div>
    </div>
  )
}
