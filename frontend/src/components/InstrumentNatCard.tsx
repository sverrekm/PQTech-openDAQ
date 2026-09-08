import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchNat, lagreNat, testNat } from '../api/instrumentnat'
import type { NatStatus, NatNett } from '../api/instrumentnat'
import { useI18n } from '../i18n'

/**
 * Instrument-NAT — noden som ruter mellom LAN og instrumentnett.
 *
 * Instrument med fastlåst IP (Elspec står alltid på 192.168.1.x og går
 * tilbake dit ved reset) kolliderer med kunde-LAN-et. Her gir vi dei ei
 * alias-adresse i staden. Dei to nettverka møtest aldri.
 */
export default function InstrumentNatCard() {
  const { t } = useI18n()
  const [namn, setNamn] = useState('')
  const [dev, setDev] = useState('wlan0')
  const [ekte, setEkte] = useState('192.168.1.0/24')
  const [alias, setAlias] = useState('10.99.0.0/24')
  const [testAdr, setTestAdr] = useState('')
  const [laddar, setLaddar] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [feil, setFeil] = useState<string | null>(null)

  const fetcher = useCallback(() => fetchNat(), [])
  const { data, refresh } = usePolling<NatStatus>(fetcher, 10000)

  const lagre = async (nett: NatNett[]) => {
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await lagreNat({ aktivert: data?.aktivert ?? true, nett })
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
    if (!ekte.trim() || !alias.trim()) {
      setFeil(t('Both the instrument subnet and the alias are required')); return
    }
    const i = data?.nett.length ?? 0
    await lagre([...(data?.nett ?? []), {
      namn: namn.trim(), grensesnitt: dev.trim() || 'wlan0',
      ekte: ekte.trim(), alias: alias.trim(),
      tabell: 99 + i, merke: 99 + i,
    }])
    setNamn('')
  }

  const fjern = async (a: string) =>
    lagre((data?.nett ?? []).filter((n) => n.alias !== a))

  const test = async () => {
    const a = testAdr.trim()
    if (!a) { setFeil(t('Enter an instrument address')); return }
    setLaddar(true); setFeil(null); setMelding(null)
    try {
      const res = await testNat({ adresse: a })
      if (res.ok) setMelding(res.melding); else setFeil(res.melding)
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLaddar(false)
    }
  }

  const inn = 'block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none'
  const merk = 'block text-xs font-medium text-gray-600 mb-1'

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h2 className="text-base font-semibold text-gray-800 mb-1">
        {t('Instrument NAT (router mode)')}
      </h2>
      <p className="text-xs text-gray-500 mb-3">
        {t('For instruments with a fixed address that collides with the LAN. The node maps the instrument subnet 1:1 onto a free alias range, so the two networks never meet — identical netmasks are fine.')}
      </p>

      {data && !data.vert_ok && (
        <div className="p-2 mb-3 bg-amber-50 border border-amber-200 rounded text-sm text-amber-800">
          {t('Could not read the host network configuration.')}
        </div>
      )}
      {feil && <div className="p-2 mb-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{feil}</div>}
      {melding && <div className="p-2 mb-3 bg-green-50 border border-green-200 rounded text-sm text-green-700">{melding}</div>}

      {data && data.nett.length > 0 && (
        <table className="w-full text-sm mb-3">
          <thead>
            <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
              <th className="py-1.5 pr-2 font-medium">{t('Use this address')}</th>
              <th className="py-1.5 pr-2 font-medium">{t('Real instrument')}</th>
              <th className="py-1.5 pr-2 font-medium">{t('Interface')}</th>
              <th className="py-1.5 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {data.nett.map((n) => (
              <tr key={n.alias} className="border-b border-gray-100">
                <td className="py-1.5 pr-2 font-mono text-xs">{n.alias}</td>
                <td className="py-1.5 pr-2 font-mono text-xs text-gray-500">{n.ekte}</td>
                <td className="py-1.5 pr-2 text-xs">
                  {n.grensesnitt}
                  <span className={'ml-2 ' + (n.aktiv ? 'text-green-700' : 'text-gray-400')}>
                    {n.aktiv ? t('active') : t('inactive')}
                  </span>
                </td>
                <td className="py-1.5 text-right">
                  <button onClick={() => fjern(n.alias)} disabled={laddar}
                    className="text-xs text-gray-500 hover:text-red-600 disabled:opacity-50">
                    {t('Remove')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="grid grid-cols-2 gap-2 mb-2">
        <div><label className={merk}>{t('Name')}</label>
          <input className={inn} value={namn} onChange={(e) => setNamn(e.target.value)}
            placeholder={t('e.g. Elspec BlackBox')} /></div>
        <div><label className={merk}>{t('Interface')}</label>
          <input className={inn} value={dev} onChange={(e) => setDev(e.target.value)} /></div>
        <div><label className={merk}>{t('Real instrument subnet')}</label>
          <input className={inn} value={ekte} onChange={(e) => setEkte(e.target.value)} /></div>
        <div><label className={merk}>{t('Alias subnet (free range)')}</label>
          <input className={inn} value={alias} onChange={(e) => setAlias(e.target.value)} /></div>
      </div>
      <button onClick={leggTil} disabled={laddar}
        className="px-3 py-1.5 text-sm bg-[#D76428] text-white rounded hover:bg-[#c05520] disabled:opacity-50 mb-3">
        {t('Set up routing')}
      </button>

      <div className="flex gap-2 items-end border-t border-gray-100 pt-3">
        <div className="flex-1">
          <label className={merk}>{t('Test via alias address')}</label>
          <input className={inn} value={testAdr} onChange={(e) => setTestAdr(e.target.value)}
            placeholder="10.99.0.1" />
        </div>
        <button onClick={test} disabled={laddar}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">
          {t('Test')}
        </button>
      </div>
    </div>
  )
}
