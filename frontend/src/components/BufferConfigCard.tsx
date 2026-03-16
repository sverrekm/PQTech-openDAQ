import { useState, useEffect } from 'react'
import type { BufferKonfig } from '../api/types'
import { fetchBufferKonfig, oppdaterBufferKonfig } from '../api/buffer'
import { useI18n } from '../i18n'

const DEFAULT_KONFIG: BufferKonfig = {
  aktivert: true,
  intervall_ms: 100,
  maks_storleik_mb: 2048,
  bevar_usynkronisert: true,
  hub_sync_intervall_sek: 60,
  hub_batch_storleik: 10000,
  hub_retensjon_dagar: 30,
}

export default function BufferConfigCard() {
  const { t } = useI18n()
  const [konfig, setKonfig] = useState<BufferKonfig>(DEFAULT_KONFIG)
  const [melding, setMelding] = useState<string | null>(null)
  const [lagrar, setLagrar] = useState(false)

  useEffect(() => {
    fetchBufferKonfig().then(setKonfig).catch(() => {})
  }, [])

  const lagre = async () => {
    setLagrar(true)
    setMelding(null)
    try {
      const res = await oppdaterBufferKonfig(konfig)
      setMelding(res.melding)
    } catch {
      setMelding(t('Error saving'))
    }
    setLagrar(false)
  }

  const oppdater = (felt: keyof BufferKonfig, verdi: number | boolean) => {
    setKonfig(prev => ({ ...prev, [felt]: verdi }))
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3">
        {t('Measurement buffer')}
      </h2>

      <div className="grid grid-cols-2 gap-4 mb-3">
        {/* Aktivert toggle */}
        <label className="flex items-center gap-2 col-span-2">
          <input
            type="checkbox"
            checked={konfig.aktivert}
            onChange={e => oppdater('aktivert', e.target.checked)}
            className="accent-[#D76428]"
          />
          <span className="text-sm text-gray-700">{t('Buffer active')}</span>
        </label>

        {/* Intervall */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('Aggregation interval (ms)')}</label>
          <input
            type="number"
            min={10}
            max={10000}
            value={konfig.intervall_ms}
            onChange={e => oppdater('intervall_ms', Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>

        {/* Maks storleik */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('Max storage (MB)')}</label>
          <input
            type="number"
            min={100}
            max={50000}
            value={konfig.maks_storleik_mb}
            onChange={e => oppdater('maks_storleik_mb', Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>

        {/* Sync intervall */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('Hub sync interval (s)')}</label>
          <input
            type="number"
            min={5}
            max={3600}
            value={konfig.hub_sync_intervall_sek}
            onChange={e => oppdater('hub_sync_intervall_sek', Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>

        {/* Batch storleik */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('Sync batch size')}</label>
          <input
            type="number"
            min={100}
            max={100000}
            value={konfig.hub_batch_storleik}
            onChange={e => oppdater('hub_batch_storleik', Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>

        {/* Retensjon */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('Hub retention (days)')}</label>
          <input
            type="number"
            min={1}
            max={365}
            value={konfig.hub_retensjon_dagar}
            onChange={e => oppdater('hub_retensjon_dagar', Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>

        {/* Bevar usynkronisert */}
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={konfig.bevar_usynkronisert}
            onChange={e => oppdater('bevar_usynkronisert', e.target.checked)}
            className="accent-[#D76428]"
          />
          <span className="text-sm text-gray-700">{t('Keep unsynced data')}</span>
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={lagre}
          disabled={lagrar}
          className="px-4 py-2 bg-[#D76428] text-white text-sm font-medium rounded-md hover:bg-[#c0571f] disabled:opacity-50 transition-colors"
        >
          {lagrar ? t('Saving...') : t('Save')}
        </button>
        {melding && (
          <span className="text-sm text-gray-600">{melding}</span>
        )}
      </div>
    </div>
  )
}
