import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import {
  fetchInstrumentNett,
  lagreInstrumentNett,
  testInstrument,
} from '../api/instrumentnett'
import type { InstrumentNettStatus, InstrumentNett } from '../api/instrumentnett'
import { useI18n } from '../i18n'

/**
 * Instrumentnett — ruter til nett som berre finst på verten.
 *
 * Containeren står på macvlan og ser difor ingenting av dei andre netta
 * verten er kopla til (instrument-wifi, USB-ethernet, isolerte segment).
 * Her legg ein inn subnetta som skal rutast via bridge-nettet.
 */
export default function InstrumentNettCard() {
  const { t } = useI18n()
  const [nyttSubnett, setNyttSubnett] = useState('')
  const [nyttNamn, setNyttNamn] = useState('')
  const [testHost, setTestHost] = useState('')
  const [laddar, setLaddar] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [feil, setFeil] = useState<string | null>(null)

  const fetcher = useCallback(() => fetchInstrumentNett(), [])
  const { data: status, refresh } = usePolling<InstrumentNettStatus>(fetcher, 10000)

  const lagre = async (nett: InstrumentNett[]) => {
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await lagreInstrumentNett({
        aktivert: status?.aktivert ?? true,
        nett,
      })
      if (res.suksess) setMelding(res.melding)
      else setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const leggTil = async () => {
    const s = nyttSubnett.trim()
    if (!s) { setFeil(t('Enter a subnet, e.g. 192.168.50.0/24')); return }
    await lagre([...(status?.nett ?? []), { subnett: s, namn: nyttNamn.trim() }])
    setNyttSubnett(''); setNyttNamn('')
  }

  const fjern = async (subnett: string) =>
    lagre((status?.nett ?? []).filter((n) => n.subnett !== subnett))

  const test = async () => {
    const h = testHost.trim()
    if (!h) { setFeil(t('Enter an instrument address')); return }
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await testInstrument({ host: h })
      if (res.ok) setMelding(res.melding)
      else setFeil(res.melding)
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const bru = status?.bru as { dev?: string; gateway?: string } | undefined

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h2 className="text-base font-semibold text-gray-800 mb-1">
        {t('Instrument networks')}
      </h2>
      <p className="text-xs text-gray-500 mb-3">
        {t('Routes to networks that only exist on the host, such as an instrument Wi-Fi or an isolated measurement segment.')}
      </p>

      {status && !status.bru_tilgjengeleg && (
        <div className="p-2 mb-3 bg-amber-50 border border-amber-200 rounded text-sm text-amber-800">
          {status.melding || t('The container has no bridge network yet.')}
        </div>
      )}
      {feil && (
        <div className="p-2 mb-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{feil}</div>
      )}
      {melding && (
        <div className="p-2 mb-3 bg-green-50 border border-green-200 rounded text-sm text-green-700">{melding}</div>
      )}

      {bru?.dev && (
        <div className="text-xs text-gray-500 mb-3">
          {t('Bridge')}: <code className="bg-gray-100 px-1 rounded">{bru.dev}</code>{' '}
          {t('via')} <code className="bg-gray-100 px-1 rounded">{bru.gateway}</code>
        </div>
      )}

      {status && status.nett.length > 0 && (
        <table className="w-full text-sm mb-3">
          <tbody>
            {status.nett.map((n) => {
              const aktiv = (status.aktive_ruter ?? []).includes(n.subnett)
              return (
                <tr key={n.subnett} className="border-b border-gray-100">
                  <td className="py-1.5 font-mono text-xs">{n.subnett}</td>
                  <td className="py-1.5 text-gray-600">{n.namn}</td>
                  <td className="py-1.5">
                    <span className={aktiv ? 'text-green-700' : 'text-gray-400'}>
                      {aktiv ? t('routed') : t('not routed')}
                    </span>
                  </td>
                  <td className="py-1.5 text-right">
                    <button
                      onClick={() => fjern(n.subnett)}
                      disabled={laddar}
                      className="text-xs text-gray-500 hover:text-red-600 disabled:opacity-50"
                    >
                      {t('Remove')}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      <div className="flex gap-2 items-end mb-3">
        <div className="flex-1">
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('Subnet')}</label>
          <input
            type="text"
            value={nyttSubnett}
            onChange={(e) => setNyttSubnett(e.target.value)}
            placeholder="192.168.50.0/24"
            className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
          />
        </div>
        <div className="flex-1">
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('Name')}</label>
          <input
            type="text"
            value={nyttNamn}
            onChange={(e) => setNyttNamn(e.target.value)}
            placeholder={t('e.g. Elspec BlackBox')}
            className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
          />
        </div>
        <button
          onClick={leggTil}
          disabled={laddar}
          className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50"
        >
          {t('Add')}
        </button>
      </div>

      <div className="flex gap-2 items-end border-t border-gray-100 pt-3">
        <div className="flex-1">
          <label className="block text-xs font-medium text-gray-600 mb-1">
            {t('Test reachability')}
          </label>
          <input
            type="text"
            value={testHost}
            onChange={(e) => setTestHost(e.target.value)}
            placeholder="192.168.50.1"
            className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none"
          />
        </div>
        <button
          onClick={test}
          disabled={laddar}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
        >
          {t('Test')}
        </button>
      </div>
    </div>
  )
}
