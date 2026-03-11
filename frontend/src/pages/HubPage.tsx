import { useState, useCallback } from 'react'
import type { HubStatus } from '../api/types'
import { leggTilNode, fjernNode, rekobleNode, fetchHubStatus } from '../api/hub'
import { usePolling } from '../hooks/usePolling'

export default function HubPage() {
  const hubFetcher = useCallback(() => fetchHubStatus(), [])
  const { data: hub, loading } = usePolling(hubFetcher, 3000)

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
        port: parseInt(nyPort) || 4840,
        protokoll: nyProtokoll,
        lokasjon: nyLokasjon.trim() || undefined,
      })
      setMelding(res.melding)
      if (res.suksess) {
        setNyAdresse('')
        setNyNamn('')
        setNyLokasjon('')
        setNyPort('4840')
        setLeggTilOpen(false)
      }
    } catch (e) {
      setMelding(`Feil: ${e}`)
    }
    setActionLoading(null)
    setTimeout(() => setMelding(null), 4000)
  }

  const handleFjern = async (id: string, namn: string) => {
    if (!confirm(`Fjern node "${namn}"?`)) return
    setActionLoading(id)
    try {
      const res = await fjernNode(id)
      setMelding(res.melding)
    } catch (e) {
      setMelding(`Feil: ${e}`)
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
      setMelding(`Feil: ${e}`)
    }
    setActionLoading(null)
    setTimeout(() => setMelding(null), 4000)
  }

  if (loading && !hub) {
    return (
      <div className="flex justify-center items-center h-32">
        <svg className="animate-spin h-5 w-5 text-[#D76428]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
    )
  }

  const hubAktiv = hub?.aktiv !== false  // hub-modus returnerer ikkje aktiv-feltet, node-modus set aktiv=false

  return (
    <>
      {/* Samandrag */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-900">openDAQ Hub</h2>
          <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium ${
            hubAktiv ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'
          }`}>
            <span className={`w-2 h-2 rounded-full ${hubAktiv ? 'bg-green-500 animate-pulse' : 'bg-yellow-500'}`} />
            {hubAktiv ? 'Hub aktiv' : 'Hub ikkje aktiv'}
          </div>
        </div>

        {!hubAktiv && (
          <div className="bg-yellow-50 border border-yellow-200 text-yellow-800 text-sm rounded-lg p-3 mb-3">
            Hub-tenesta køyrer ikkje. Nodar kan konfigurerast her, men tilkoblingar vert fyrst aktive
            når containeren startar med <code className="bg-yellow-100 px-1 rounded">OPENDAQ_MODUS=hub</code>.
          </div>
        )}

        <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-2">
          <InfoCell label="Tilkobla nodar" value={`${hub?.tilkobla_nodar ?? 0} / ${hub?.totalt_nodar ?? 0}`} />
          <InfoCell label="Totalt kanalar" value={String(hub?.totalt_kanalar ?? 0)} />
          <InfoCell label="Hub IP" value={hub?.ip ?? '-'} />
          <InfoCell label="OPC-UA" value={hub?.ip ? `opc.tcp://${hub.ip}:4840/` : '-'} small />
          <InfoCell label="NativeStreaming" value={hub?.ip ? `daq.nd://${hub.ip}:7420/` : '-'} small />
        </div>
      </div>

      {/* Meldingsbanner */}
      {melding && (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 text-sm rounded-lg p-3 mb-4">
          {melding}
        </div>
      )}

      {/* Node-liste */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">Fjern-nodar</h3>
          <button
            onClick={() => setLeggTilOpen(!leggTilOpen)}
            className="text-xs font-medium px-3 py-1.5 rounded-md bg-[#D76428] text-white hover:bg-[#c55a23] transition-colors"
          >
            {leggTilOpen ? 'Avbryt' : '+ Legg til node'}
          </button>
        </div>

        {/* Legg til-skjema */}
        {leggTilOpen && (
          <div className="border border-gray-200 rounded-lg p-3 mb-4 bg-gray-50">
            <div className="grid grid-cols-2 gap-3 mb-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">IP-adresse *</label>
                <input
                  type="text" value={nyAdresse} onChange={e => setNyAdresse(e.target.value)}
                  placeholder="10.0.0.5"
                  className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Namn</label>
                <input
                  type="text" value={nyNamn} onChange={e => setNyNamn(e.target.value)}
                  placeholder="Sundet - Tavle 3"
                  className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Lokasjon</label>
                <input
                  type="text" value={nyLokasjon} onChange={e => setNyLokasjon(e.target.value)}
                  placeholder="Bygning A, 2. etasje"
                  className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Port</label>
                  <input
                    type="number" value={nyPort} onChange={e => setNyPort(e.target.value)}
                    className="w-full text-sm border border-gray-300 rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Protokoll</label>
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
              {actionLoading === 'add' ? 'Koplar til...' : 'Legg til og koble til'}
            </button>
          </div>
        )}

        {/* Node-tabell */}
        {(!hub?.nodar || hub.nodar.length === 0) ? (
          <p className="text-sm text-gray-500 py-4 text-center">Ingen nodar konfigurert. Legg til ein fjern-node for å starte.</p>
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

      {/* Logg */}
      <HubLogViewer />
    </>
  )
}

function NodeRow({ node, actionLoading, onRekoble, onFjern }: {
  node: HubStatus['nodar'][0]
  actionLoading: string | null
  onRekoble: (id: string) => void
  onFjern: (id: string, namn: string) => void
}) {
  const statusColor = node.tilkobla ? 'bg-green-500' : 'bg-red-500'
  const statusText = node.tilkobla ? 'Tilkobla' : 'Fråkobla'
  const isLoading = actionLoading === node.id

  return (
    <div className="border border-gray-200 rounded-lg p-3 flex items-center gap-3">
      {/* Status-indikator */}
      <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusColor} ${node.tilkobla ? 'animate-pulse' : ''}`} />

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-gray-900 truncate">{node.namn}</span>
          <span className="text-xs text-gray-400">{node.adresse}:{node.port}</span>
        </div>
        <div className="flex items-center gap-3 mt-0.5">
          <span className={`text-xs ${node.tilkobla ? 'text-green-600' : 'text-red-600'}`}>{statusText}</span>
          {node.lokasjon && <span className="text-xs text-gray-400">{node.lokasjon}</span>}
          {node.tilkobla && <span className="text-xs text-gray-500">{node.antal_kanalar} kanalar</span>}
          {node.feil && !node.tilkobla && <span className="text-xs text-red-500 truncate">{node.feil}</span>}
        </div>
      </div>

      {/* Aksjonar */}
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <button
          onClick={() => onRekoble(node.id)}
          disabled={isLoading}
          className="text-xs px-2.5 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
          title="Tving rekobling"
        >
          {isLoading ? '...' : 'Rekoble'}
        </button>
        <button
          onClick={() => onFjern(node.id, node.namn)}
          disabled={isLoading}
          className="text-xs px-2.5 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-50 transition-colors"
          title="Fjern node"
        >
          Fjern
        </button>
      </div>
    </div>
  )
}

function InfoCell({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <div className="bg-gray-50 rounded-md px-2.5 py-1.5">
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className={`mt-0.5 font-semibold text-gray-900 ${small ? 'text-xs' : 'text-sm'}`}>
        {value || '-'}
      </div>
    </div>
  )
}

function HubLogViewer() {
  const logFetcher = useCallback(() =>
    fetch('/api/hub/logg', { credentials: 'include' }).then(r => r.json()),
    []
  )
  const { data } = usePolling(logFetcher, 3000)
  const linjer: string[] = data?.linjer ?? []

  if (linjer.length === 0) return null

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-2">Hub-logg</h3>
      <div className="bg-gray-900 text-gray-300 text-xs font-mono p-3 rounded-lg max-h-48 overflow-y-auto">
        {linjer.slice(-50).map((l, i) => (
          <div key={i} className="whitespace-pre-wrap">{l}</div>
        ))}
      </div>
    </div>
  )
}
