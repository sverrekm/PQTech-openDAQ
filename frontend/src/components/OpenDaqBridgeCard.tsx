import { useState, useEffect, useCallback } from 'react'
import { fetchOpenDaqStatus, restartOpenDaq, fetchDeviceIdx, settDeviceIdx } from '../api/opendaq'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'
import CopyableCommand from './CopyableCommand'
import { useI18n } from '../i18n'

function PortStatusDot({ ok }: { ok?: boolean }) {
  if (ok === undefined) return <span className="text-gray-500">?</span>
  return (
    <span className={`inline-block w-2.5 h-2.5 rounded-full mr-1.5 shadow-lg ${ok ? 'bg-green-500 shadow-green-500/50' : 'bg-red-500 shadow-red-500/50'}`} />
  )
}

export default function OpenDaqBridgeCard() {
  const { t, lang } = useI18n()
  const fetcher = useCallback(() => fetchOpenDaqStatus(), [])
  const { data: s, refresh, loading } = usePolling(fetcher, 3000)
  const [restartMsg, setRestartMsg] = useState<{ text: string; ok: boolean } | null>(null)
  // openDAQ-rota er daqref://device<idx>. To nodar med same indeks får same
  // lokale device-ID, og hubben kan då berre halde den eine.
  const [devIdx, setDevIdx] = useState<string>('')
  const [idxBusy, setIdxBusy] = useState(false)
  const [idxMsg, setIdxMsg] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => {
    fetchDeviceIdx().then(d => setDevIdx(String(d.device_idx))).catch(() => {})
  }, [])

  const lagreIdx = async () => {
    const n = parseInt(devIdx)
    if (isNaN(n) || n < 0 || n > 1) {
      setIdxMsg({ text: t('The index must be 0 or 1 — daqref only provides device0 and device1.'), ok: false })
      return
    }
    setIdxBusy(true); setIdxMsg(null)
    try {
      const res = await settDeviceIdx(n)
      setIdxMsg({ text: res.melding, ok: res.suksess })
      refresh()
    } catch (e) {
      setIdxMsg({ text: String(e), ok: false })
    }
    setIdxBusy(false)
  }

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

  if (!s) return null

  const handleRestart = async () => {
    try {
      const res = await restartOpenDaq()
      setRestartMsg({ text: res.melding, ok: res.suksess })
      refresh()
    } catch (e) {
      setRestartMsg({ text: String(e), ok: false })
    }
  }

  const ps = s.port_status
  const alleOppe = s.alle_portar_oppe === true

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 mb-3 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">{t('openDAQ Network Servers')}</h2>

      <div className={`flex items-center gap-2 p-2 rounded-lg mb-3 ${alleOppe ? 'bg-green-100 border border-green-500' : s.aktiv ? 'bg-yellow-100 border border-yellow-500' : 'bg-red-100 border border-red-500'}`}>
        <span className={`inline-block w-3.5 h-3.5 rounded-full ${alleOppe ? 'bg-green-500 shadow-lg shadow-green-500/50' : s.aktiv ? 'bg-yellow-500' : 'bg-red-500'}`} />
        <span className={`font-semibold ${alleOppe ? 'text-green-700' : s.aktiv ? 'text-yellow-700' : 'text-red-700'}`}>
          {alleOppe ? t('All servers up') : s.aktiv ? t('Partially active') : t('Inactive')}
        </span>
        {s.startet && alleOppe && (
          <span className="text-xs text-gray-500 ml-auto">
            {t('Since')} {new Date(s.startet).toLocaleTimeString(lang === 'nb' ? 'nb-NO' : 'en-US')}
          </span>
        )}
      </div>

      <InfoGrid items={[
        { label: t('Device'), value: s.enhet_namn || '-' },
        { label: t('Channels'), value: s.kanalar?.length || '-' },
      ]} />

      {s.feil && !s.aktiv && (
        <div className="mt-3 px-3 py-2 rounded-lg text-sm bg-red-100 text-red-800">{s.feil}</div>
      )}

      {ps && (
        <div className="my-2">
          <div className="text-sm text-gray-500 mb-1.5">
            {t('Live port verification:')}
          </div>
          {[
            { namn: 'OPC-UA', key: 'opcua' as const, port: s.porter?.opcua || 4840 },
            { namn: 'Native Streaming', key: 'native_streaming' as const, port: s.porter?.native_streaming || 7420 },
            { namn: 'WebSocket/LT', key: 'websocket' as const, port: s.porter?.websocket || 7414 },
          ].map(srv => (
            <div key={srv.key} className="flex items-center py-1 text-sm">
              <PortStatusDot ok={ps[srv.key]} />
              <span className="min-w-[100px]">{srv.namn}</span>
              <span className="text-gray-500 text-xs ml-auto">
                {s.ip ? `${s.ip}:${srv.port}` : `:${srv.port}`}
              </span>
            </div>
          ))}
        </div>
      )}

      {s.aktiv && s.ip && (
        <>
          <CopyableCommand text={s.ip} className="mt-3" />
          <p className="text-gray-500 text-xs mt-2">
            {t('The device appears automatically as "Detected device" in DewesoftX Setup > Devices.')}
          </p>
        </>
      )}
      <div className="mt-3">
        <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out" onClick={handleRestart}>{t('Restart')}</button>
        {restartMsg && (
          <span className={`text-sm ml-2 ${restartMsg.ok ? 'text-green-700' : 'text-red-700'}`}>
            {restartMsg.text}
          </span>
        )}
      </div>
      <div className="border-t border-gray-100 mt-3 pt-3">
        <label className="block text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">
          {t('openDAQ device index')}
        </label>
        <p className="text-xs text-gray-500 mb-2 leading-snug">
          {t('The root device is daqref://device<index>, and daqref only provides device0 and device1. The index does not have to be unique across nodes — the hub gives every node its own openDAQ instance. It only decides what the root is called (RefDev0 / RefDev1) towards DewesoftX.')}
        </p>
        <div className="flex gap-2 items-center">
          <input
            type="number" min={0} max={1} value={devIdx}
            onChange={e => setDevIdx(e.target.value)}
            className="w-24 text-sm px-3 py-1.5 rounded-md border border-gray-300 focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"
          />
          <button
            onClick={lagreIdx} disabled={idxBusy}
            className="bg-[#D76428] text-white text-sm px-4 py-1.5 rounded-md hover:bg-[#c25a24] disabled:opacity-50"
          >
            {idxBusy ? t('Saving...') : t('Save and rebuild bridge')}
          </button>
        </div>
        {idxMsg && (
          <p className={`text-sm mt-2 ${idxMsg.ok ? 'text-green-700' : 'text-red-600'}`}>{idxMsg.text}</p>
        )}
      </div>

    </div>
  )
}
