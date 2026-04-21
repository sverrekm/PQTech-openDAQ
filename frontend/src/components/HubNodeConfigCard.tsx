import { useState, useCallback } from 'react'
import type { HubStatus, NodeType, ModbusRegister, ModbusTestResultat } from '../api/types'
import { leggTilNode, fjernNode, rekobleNode, fetchHubStatus, testModbusTilkobling } from '../api/hub'
import { usePolling } from '../hooks/usePolling'
import { useI18n } from '../i18n'
import ModbusRegisterTable from './ModbusRegisterTable'

export default function HubNodeConfigCard() {
  const { t } = useI18n()
  const hubFetcher = useCallback(() => fetchHubStatus(), [])
  const { data: hub } = usePolling(hubFetcher, 3000)

  // I direkte-modus (ikkje hub) er berre modbus_tcp relevant
  const erHubModus = hub?.modus === 'hub'
  const standardType: NodeType = erHubModus ? 'opendaq' : 'modbus_tcp'

  const [nyType, setNyType] = useState<NodeType>(standardType)
  const [nyAdresse, setNyAdresse] = useState('')
  const [nyNamn, setNyNamn] = useState('')
  const [nyLokasjon, setNyLokasjon] = useState('')
  const [nyPort, setNyPort] = useState(erHubModus ? '4840' : '502')
  const [nyProtokoll, setNyProtokoll] = useState('daq.opcua')
  const [nyUnitId, setNyUnitId] = useState('1')
  const [nyPollHz, setNyPollHz] = useState('1')
  const [nyTimeoutMs, setNyTimeoutMs] = useState('2000')
  const [nyRegisters, setNyRegisters] = useState<ModbusRegister[]>([])

  const [leggTilOpen, setLeggTilOpen] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [testResultat, setTestResultat] = useState<ModbusTestResultat[] | null>(null)

  const byttType = (t: NodeType) => {
    setNyType(t)
    setNyPort(t === 'modbus_tcp' ? '502' : '4840')
    setTestResultat(null)
  }

  const nullstillSkjema = () => {
    setNyAdresse('')
    setNyNamn('')
    setNyLokasjon('')
    setNyPort(nyType === 'modbus_tcp' ? '502' : '4840')
    setNyUnitId('1')
    setNyPollHz('1')
    setNyTimeoutMs('2000')
    setNyRegisters([])
    setTestResultat(null)
  }

  const handleTest = async () => {
    if (!nyAdresse.trim()) return
    setActionLoading('test')
    setTestResultat(null)
    try {
      const res = await testModbusTilkobling({
        host: nyAdresse.trim(),
        port: parseInt(nyPort) || 502,
        unit_id: parseInt(nyUnitId) || 1,
        timeout_ms: parseInt(nyTimeoutMs) || 2000,
        registers: nyRegisters,
      })
      setMelding(res.melding)
      setTestResultat(res.verdiar)
    } catch (e) {
      setMelding(`${t('Error')}: ${e}`)
    }
    setActionLoading(null)
  }

  const handleLeggTil = async () => {
    if (!nyAdresse.trim()) return
    setActionLoading('add')
    try {
      const payload: Parameters<typeof leggTilNode>[0] = {
        adresse: nyAdresse.trim(),
        namn: nyNamn.trim() || undefined,
        port: parseInt(nyPort) || (nyType === 'modbus_tcp' ? 502 : 4840),
        lokasjon: nyLokasjon.trim() || undefined,
        type: nyType,
      }
      if (nyType === 'opendaq') {
        payload.protokoll = nyProtokoll
      } else {
        payload.modbus_unit_id = parseInt(nyUnitId) || 1
        payload.modbus_poll_hz = parseFloat(nyPollHz) || 1
        payload.modbus_timeout_ms = parseInt(nyTimeoutMs) || 2000
        payload.modbus_registers = nyRegisters
      }
      const res = await leggTilNode(payload)
      setMelding(res.melding)
      if (res.suksess) {
        nullstillSkjema()
        setLeggTilOpen(false)
      }
    } catch (e) {
      setMelding(`${t('Error')}: ${e}`)
    }
    setActionLoading(null)
    setTimeout(() => setMelding(null), 6000)
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
          {/* Node type toggle (berre synleg i hub-modus — direkte-modus er alltid modbus) */}
          {erHubModus && (
            <div className="mb-3">
              <label className="block text-xs text-gray-500 mb-1">{t('Node type')}</label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => byttType('opendaq')}
                  className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                    nyType === 'opendaq'
                      ? 'bg-[#D76428] text-white border-[#D76428]'
                      : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-100'
                  }`}
                >
                  openDAQ
                </button>
                <button
                  type="button"
                  onClick={() => byttType('modbus_tcp')}
                  className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                    nyType === 'modbus_tcp'
                      ? 'bg-[#D76428] text-white border-[#D76428]'
                      : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-100'
                  }`}
                >
                  Modbus TCP
                </button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 mb-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('IP address *')}</label>
              <input
                type="text" value={nyAdresse} onChange={e => setNyAdresse(e.target.value)}
                placeholder={nyType === 'modbus_tcp' ? '192.168.1.81' : '10.0.0.5'}
                className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('Name')}</label>
              <input
                type="text" value={nyNamn} onChange={e => setNyNamn(e.target.value)}
                placeholder={nyType === 'modbus_tcp' ? 'PQube 3' : 'Sundet - Tavle 3'}
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
              {nyType === 'opendaq' ? (
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
              ) : (
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('Unit ID')}</label>
                  <input
                    type="number" min="0" max="255" value={nyUnitId}
                    onChange={e => setNyUnitId(e.target.value)}
                    className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                  />
                </div>
              )}
            </div>
          </div>

          {/* Modbus-spesifikke felt */}
          {nyType === 'modbus_tcp' && (
            <>
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('Poll rate (Hz)')}</label>
                  <input
                    type="number" step="0.1" min="0.1" max="100" value={nyPollHz}
                    onChange={e => setNyPollHz(e.target.value)}
                    className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('Timeout (ms)')}</label>
                  <input
                    type="number" min="100" max="30000" value={nyTimeoutMs}
                    onChange={e => setNyTimeoutMs(e.target.value)}
                    className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                  />
                </div>
              </div>

              <div className="mb-3">
                <ModbusRegisterTable registers={nyRegisters} onChange={setNyRegisters} />
              </div>

              <div className="flex gap-2 mb-3">
                <button
                  type="button"
                  onClick={handleTest}
                  disabled={!nyAdresse.trim() || actionLoading === 'test'}
                  className="text-sm font-medium px-4 py-2 rounded-md bg-gray-200 text-gray-700 hover:bg-gray-300 disabled:opacity-50 transition-colors"
                >
                  {actionLoading === 'test' ? t('Testing...') : t('Test connection')}
                </button>
              </div>

              {/* Test-resultat */}
              {testResultat && testResultat.length > 0 && (
                <div className="border border-gray-200 rounded-lg p-2 mb-3 bg-white">
                  <h5 className="text-xs font-semibold text-gray-700 mb-1">{t('Test results')}</h5>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-gray-500 border-b border-gray-200">
                        <th className="py-1">{t('Name')}</th>
                        <th className="py-1">{t('Address')}</th>
                        <th className="py-1">{t('Raw')}</th>
                        <th className="py-1">{t('Value')}</th>
                        <th className="py-1">{t('Error')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {testResultat.map((r, i) => (
                        <tr key={i} className="border-b border-gray-100 last:border-0">
                          <td className="py-0.5">{r.namn}</td>
                          <td className="py-0.5">{r.adresse}</td>
                          <td className="py-0.5 font-mono">{r.raa ? `[${r.raa.join(',')}]` : '—'}</td>
                          <td className="py-0.5 font-mono">{r.verdi !== null ? r.verdi.toFixed(3) : '—'}</td>
                          <td className="py-0.5 text-red-600">{r.feil || ''}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

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
  const isModbus = node.type === 'modbus_tcp'

  return (
    <div className="border border-gray-200 rounded-lg p-3 flex items-center gap-3">
      <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusColor} ${node.tilkobla ? 'animate-pulse' : ''}`} />

      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-gray-900 truncate">{node.namn}</span>
          <span className="text-xs text-gray-400">{node.adresse}:{node.port}</span>
          {isModbus && (
            <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">
              Modbus TCP
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 mt-0.5">
          <span className={`text-xs ${node.tilkobla ? 'text-green-600' : 'text-red-600'}`}>{statusText}</span>
          {node.lokasjon && <span className="text-xs text-gray-400">{node.lokasjon}</span>}
          <span className="text-xs text-gray-500">
            {node.antal_kanalar} {isModbus ? t('registers') : t('channels')}
          </span>
          {isModbus && node.modbus_poll_hz !== undefined && (
            <span className="text-xs text-gray-400">{node.modbus_poll_hz} Hz</span>
          )}
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
