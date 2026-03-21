import { useState, useCallback } from 'react'
import type { HubStatus } from '../api/types'
import { leggTilNode, fjernNode, rekobleNode, fetchHubStatus } from '../api/hub'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'

export default function HubNodeConfigCard() {
  const { t } = useI18n()
  const hubFetcher = useCallback(() => fetchHubStatus(), [])
  const { data: hub } = usePolling(hubFetcher, 3000)

  const [nyAdresse, setNyAdresse] = useState('')
  const [nyNamn, setNyNamn] = useState('')
  const [nyLokasjon, setNyLokasjon] = useState('')
  const [nyPort, setNyPort] = useState('4840')
  const [nyProtokoll, setNyProtokoll] = useState('daq.opcua')
  const [leggTilOpen, setLeggTilOpen] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const handleLeggTil = async () => {
    if (!nyAdresse.trim()) return
    setActionLoading('add')
    try {
      const res = await leggTilNode({
        adresse: nyAdresse.trim(),
        namn: nyNamn.trim() || undefined,
        port: parseInt(nyPort) || 7420,
        protokoll: nyProtokoll,
        lokasjon: nyLokasjon.trim() || undefined,
      })
      setMelding(res.melding)
      if (res.suksess) {
        setNyAdresse('')
        setNyNamn('')
        setNyLokasjon('')
        setNyPort('7420')
        setLeggTilOpen(false)
      }
    } catch (e) {
      setMelding(`${t('Error')}: ${e}`)
    }
    setActionLoading(null)
    setTimeout(() => setMelding(null), 4000)
  }

  const handleFjern = async (id: string, namn: string) => {
    if (!confirm(`${t('Remove')} "${namn}"?`)) return
    setActionLoading(id)
    try {
      const res = await fjernNode(id)
      setMelding(res.melding)
    } catch (e) {
      setMelding(`${t('Error')}: ${e}`)
    }
    setActionLoading(null)
    setTimeout(() => setMelding(null), 4000)
  }

  const handleRekoble = async (id: string) => {
    setActionLoading(id)
    try {
      const res = await rekobleNode(id)
      setMelding(res.melding)
    } catch (e) {
      setMelding(`${t('Error')}: ${e}`)
    }
    setActionLoading(null)
    setTimeout(() => setMelding(null), 4000)
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">{t('Remote nodes')}</h3>
        <button
          onClick={() => setLeggTilOpen(!leggTilOpen)}
          className="text-xs font-medium px-3 py-1.5 rounded-md bg-[#D76428] text-white hover:bg-[#c55a23] transition-colors"
        >
          {leggTilOpen ? t('Cancel') : t('+ Add node')}
        </button>
      </div>

      {/* Add form */}
      {leggTilOpen && (
        <div className="border border-gray-200 rounded-lg p-3 mb-4 bg-gray-50">
          <div className="grid grid-cols-2 gap-3 mb-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('IP address *')}</label>
              <input
                type="text" value={nyAdresse} onChange={e => setNyAdresse(e.target.value)}
                placeholder="10.0.0.5"
                className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('Name')}</label>
              <input
                type="text" value={nyNamn} onChange={e => setNyNamn(e.target.value)}
                placeholder="Sundet - Tavle 3"
                className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('Location')}</label>
              <input
                type="text" value={nyLokasjon} onChange={e => setNyLokasjon(e.target.value)}
                placeholder="Bygning A, 2. etasje"
                className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('Port')}</label>
                <input
                  type="number" value={nyPort} onChange={e => setNyPort(e.target.value)}
                  className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('Protocol')}</label>
                <select
                  value={nyProtokoll} onChange={e => setNyProtokoll(e.target.value)}
                  className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                >
                  <option value="daq.opcua">OPC-UA</option>
                  <option value="daq.nd">NativeStreaming</option>
                </select>
              </div>
            </div>
          </div>
          <button
            onClick={handleLeggTil}
            disabled={!nyAdresse.trim() || actionLoading === 'add'}
            className="text-sm font-medium px-4 py-2 rounded-md bg-[#D76428] text-white hover:bg-[#c55a23] disabled:opacity-50 transition-colors"
          >
            {actionLoading === 'add' ? t('Connecting to node...') : t('Add and connect')}
          </button>
        </div>
      )}

      {/* Message banner */}
      {melding && (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 text-sm rounded-lg p-3 mb-3">
          {melding}
        </div>
      )}

      {/* Node list */}
      {(!hub?.nodar || hub.nodar.length === 0) ? (
        <p className="text-sm text-gray-500 py-4 text-center">{t('No nodes configured. Add a remote node to get started.')}</p>
      ) : (
        <div className="space-y-2">
          {hub.nodar.map(node => (
            <NodeRow
              key={node.id}
              node={node}
              actionLoading={actionLoading}
              onRekoble={handleRekoble}
              onFjern={handleFjern}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function NodeRow({ node, actionLoading, onRekoble, onFjern }: {
  node: HubStatus['nodar'][0]
  actionLoading: string | null
  onRekoble: (id: string) => void
  onFjern: (id: string, namn: string) => void
}) {
  const { t } = useI18n()
  const statusColor = node.tilkobla ? 'bg-green-500' : 'bg-red-500'
  const statusText = node.tilkobla ? t('Connected') : t('Disconnected')
  const isLoading = actionLoading === node.id

  return (
    <div className="border border-gray-200 rounded-lg p-3 flex items-center gap-3">
      <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusColor} ${node.tilkobla ? 'animate-pulse' : ''}`} />

      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-gray-900 truncate">{node.namn}</span>
          <span className="text-xs text-gray-400">{node.adresse}:{node.port}</span>
        </div>
        <div className="flex items-center gap-3 mt-0.5">
          <span className={`text-xs ${node.tilkobla ? 'text-green-600' : 'text-red-600'}`}>{statusText}</span>
          {node.lokasjon && <span className="text-xs text-gray-400">{node.lokasjon}</span>}
          {node.tilkobla && <span className="text-xs text-gray-500">{node.antal_kanalar} {t('channels')}</span>}
          {node.feil && !node.tilkobla && <span className="text-xs text-red-500 truncate">{node.feil}</span>}
        </div>
      </div>

      <div className="flex items-center gap-1.5 flex-shrink-0">
        <button
          onClick={() => onRekoble(node.id)}
          disabled={isLoading}
          className="text-xs px-2.5 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
          title={t('Force reconnect')}
        >
          {isLoading ? '...' : t('Reconnect')}
        </button>
        <button
          onClick={() => onFjern(node.id, node.namn)}
          disabled={isLoading}
          className="text-xs px-2.5 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-50 transition-colors"
          title={t('Remove')}
        >
          {t('Remove')}
        </button>
      </div>
    </div>
  )
}
