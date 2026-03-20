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
  sample_rate: 20000,
  ssd_sti: '',
  ram_buffer_sekund: 30,
  hendingar_aktivert: true,
  rms_terskel_prosent: 150,
  dvdt_terskel: 0.1,
  mqtt_endring_terskel: 5.0,
  pre_trigger_ms: 1000,
  post_trigger_ms: 2000,
  mqtt_logg_aktivert: true,
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

  const oppdater = (felt: keyof BufferKonfig, verdi: number | boolean | string) => {
    setKonfig(prev => ({ ...prev, [felt]: verdi }))
  }

  const ramMB = ((konfig.sample_rate * konfig.ram_buffer_sekund * 8 * 8) / 1e6).toFixed(0)

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

        {/* Sample rate */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('Sample rate')} (Hz)</label>
          <input
            type="number"
            min={1000}
            max={200000}
            step={1000}
            value={konfig.sample_rate}
            onChange={e => oppdater('sample_rate', Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>

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

        {/* SSD-sti */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">{t('SSD path')} <span className="text-gray-400">({t('empty = auto-detect')})</span></label>
          <input
            type="text"
            value={konfig.ssd_sti}
            onChange={e => oppdater('ssd_sti', e.target.value)}
            placeholder="/data/ssd"
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>
      </div>

      {/* RAM ringbuffer */}
      <div className="border-t border-gray-200 pt-3 mt-3 mb-3">
        <h3 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">
          {t('RAM ring buffer')}
        </h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('Buffer duration (s)')}</label>
            <input
              type="number"
              min={5}
              max={120}
              value={konfig.ram_buffer_sekund}
              onChange={e => oppdater('ram_buffer_sekund', Number(e.target.value))}
              className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
            />
          </div>
          <div className="flex items-end">
            <span className="text-xs text-gray-500 pb-2">~{ramMB} MB RAM</span>
          </div>
        </div>
      </div>

      {/* Hendingsdeteksjon */}
      <div className="border-t border-gray-200 pt-3 mt-3 mb-3">
        <h3 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">
          {t('Event detection')}
        </h3>
        <label className="flex items-center gap-2 mb-2">
          <input
            type="checkbox"
            checked={konfig.hendingar_aktivert}
            onChange={e => oppdater('hendingar_aktivert', e.target.checked)}
            className="accent-[#D76428]"
          />
          <span className="text-sm text-gray-700">{t('Event detection active')}</span>
        </label>

        {konfig.hendingar_aktivert && (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('RMS threshold (%)')}</label>
              <input
                type="number"
                min={101}
                max={1000}
                value={konfig.rms_terskel_prosent}
                onChange={e => oppdater('rms_terskel_prosent', Number(e.target.value))}
                className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('dV/dt threshold (V/ms)')}</label>
              <input
                type="number"
                min={0.001}
                max={100}
                step={0.01}
                value={konfig.dvdt_terskel}
                onChange={e => oppdater('dvdt_terskel', Number(e.target.value))}
                className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('MQTT change threshold')}</label>
              <input
                type="number"
                min={0.1}
                max={1000}
                step={0.1}
                value={konfig.mqtt_endring_terskel}
                onChange={e => oppdater('mqtt_endring_terskel', Number(e.target.value))}
                className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('Pre-trigger (ms)')}</label>
              <input
                type="number"
                min={100}
                max={30000}
                value={konfig.pre_trigger_ms}
                onChange={e => oppdater('pre_trigger_ms', Number(e.target.value))}
                className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('Post-trigger (ms)')}</label>
              <input
                type="number"
                min={100}
                max={30000}
                value={konfig.post_trigger_ms}
                onChange={e => oppdater('post_trigger_ms', Number(e.target.value))}
                className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
              />
            </div>
          </div>
        )}
      </div>

      {/* MQTT-logging */}
      <div className="border-t border-gray-200 pt-3 mt-3 mb-3">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={konfig.mqtt_logg_aktivert}
            onChange={e => oppdater('mqtt_logg_aktivert', e.target.checked)}
            className="accent-[#D76428]"
          />
          <span className="text-sm text-gray-700">{t('MQTT logging')}</span>
        </label>
      </div>

      {/* Hub sync settings */}
      <div className="border-t border-gray-200 pt-3 mt-3 mb-3">
        <h3 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">
          Hub sync
        </h3>
        <div className="grid grid-cols-2 gap-4">
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
