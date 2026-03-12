import { useState, useEffect, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchMqttKonfig, oppdaterMqttKonfig, fetchMqttStatus } from '../api/mqtt'
import type { MqttKonfig, MqttKanalKonfig, MqttStatus } from '../api/types'
import { useI18n } from '../i18n'

const TOM_KANAL: MqttKanalKonfig = {
  topic: '',
  namn: '',
  enhet: '',
  json_sti: '',
  range_min: -1000,
  range_max: 1000,
}

const TOM_KONFIG: MqttKonfig = {
  broker: {
    host: '',
    port: 1883,
    brukarnavn: '',
    passord: '',
    klient_id: 'pqtech-opendaq',
    aktivert: false,
  },
  kanalar: [],
}

export default function MqttSettingsCard() {
  const { t } = useI18n()
  const [konfig, setKonfig] = useState<MqttKonfig>(TOM_KONFIG)
  const [lasta, setLasta] = useState(false)
  const [lagrar, setLagrar] = useState(false)
  const [melding, setMelding] = useState<string | null>(null)
  const [feil, setFeil] = useState<string | null>(null)

  useEffect(() => {
    fetchMqttKonfig()
      .then((k) => {
        setKonfig(k)
        setLasta(true)
      })
      .catch((e) => {
        setFeil(e.message)
        setLasta(true)
      })
  }, [])

  const statusFetcher = useCallback(() => fetchMqttStatus(), [])
  const { data: status } = usePolling<MqttStatus>(statusFetcher, 3000)

  const oppdaterBroker = (felt: string, verdi: string | number | boolean) => {
    setKonfig((prev) => ({
      ...prev,
      broker: { ...prev.broker, [felt]: verdi },
    }))
    setMelding(null)
  }

  const leggTilKanal = () => {
    setKonfig((prev) => ({
      ...prev,
      kanalar: [...prev.kanalar, { ...TOM_KANAL }],
    }))
    setMelding(null)
  }

  const fjernKanal = (idx: number) => {
    setKonfig((prev) => ({
      ...prev,
      kanalar: prev.kanalar.filter((_, i) => i !== idx),
    }))
    setMelding(null)
  }

  const oppdaterKanal = (idx: number, felt: string, verdi: string | number) => {
    setKonfig((prev) => ({
      ...prev,
      kanalar: prev.kanalar.map((k, i) =>
        i === idx ? { ...k, [felt]: verdi } : k
      ),
    }))
    setMelding(null)
  }

  const lagre = async () => {
    setLagrar(true)
    setFeil(null)
    setMelding(null)
    try {
      const res = await oppdaterMqttKonfig(konfig)
      if (res.suksess) {
        setMelding(res.melding)
      } else {
        setFeil(res.melding)
      }
    } catch (e) {
      setFeil(e instanceof Error ? e.message : String(e))
    } finally {
      setLagrar(false)
    }
  }

  if (!lasta) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900 mb-3">{t('MQTT channels')}</h2>
        <p className="text-sm text-gray-500">{t('Loading...')}</p>
      </div>
    )
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900 mb-1">{t('MQTT channels')}</h2>
      <p className="text-xs text-gray-500 mb-4">
        {t('Subscribe to MQTT topics and display them as extra channels in the system.')}
      </p>

      {status && (
        <div className="flex items-center gap-2 mb-4 text-xs">
          <span
            className={`inline-block w-2 h-2 rounded-full ${
              status.tilkobla
                ? 'bg-green-500'
                : status.aktivert
                  ? 'bg-yellow-500'
                  : 'bg-gray-400'
            }`}
          />
          <span className="text-gray-600">
            {status.tilkobla
              ? `${t('Connected')} (${status.meldingar_motteke} ${t('messages')})`
              : status.aktivert
                ? status.feil || t('Connecting to broker...')
                : t('Disabled')}
          </span>
        </div>
      )}

      <div className="mb-4 p-3 bg-gray-50 rounded-lg">
        <h3 className="text-sm font-medium text-gray-700 mb-2">{t('Broker connection')}</h3>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('Host / IP')}</label>
            <input
              type="text"
              className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] focus:border-[#D76428] outline-none"
              value={konfig.broker.host}
              onChange={(e) => oppdaterBroker('host', e.target.value)}
              placeholder="192.168.1.100"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('Port')}</label>
            <input
              type="number"
              className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] focus:border-[#D76428] outline-none"
              value={konfig.broker.port}
              onChange={(e) => oppdaterBroker('port', parseInt(e.target.value) || 1883)}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('Username')}</label>
            <input
              type="text"
              className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] focus:border-[#D76428] outline-none"
              value={konfig.broker.brukarnavn}
              onChange={(e) => oppdaterBroker('brukarnavn', e.target.value)}
              placeholder={t('(optional)')}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('Password')}</label>
            <input
              type="password"
              className="block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] focus:border-[#D76428] outline-none"
              value={konfig.broker.passord}
              onChange={(e) => oppdaterBroker('passord', e.target.value)}
              placeholder={t('(optional)')}
            />
          </div>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              className="h-4 w-4 accent-[#D76428] rounded"
              checked={konfig.broker.aktivert}
              onChange={(e) => oppdaterBroker('aktivert', e.target.checked)}
            />
            {t('Enable MQTT')}
          </label>
        </div>
      </div>

      <div className="mb-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-gray-700">
            {t('Topics')} ({konfig.kanalar.length})
          </h3>
          <button
            className="px-2 py-1 text-xs bg-[#D76428] text-white rounded hover:bg-[#c05520] transition-colors"
            onClick={leggTilKanal}
          >
            {t('+ Add')}
          </button>
        </div>

        {konfig.kanalar.length === 0 ? (
          <p className="text-xs text-gray-400 italic">
            {t('No topics configured. Click "Add" to start.')}
          </p>
        ) : (
          <div className="space-y-2">
            {konfig.kanalar.map((k, i) => (
              <div
                key={i}
                className="p-2 bg-gray-50 rounded-lg border border-gray-200"
              >
                <div className="grid grid-cols-[1fr_1fr_auto] gap-2 mb-1">
                  <div>
                    <label className="block text-xs text-gray-500 mb-0.5">{t('Topic')}</label>
                    <input
                      type="text"
                      className="block w-full rounded border border-gray-300 px-2 py-1 text-xs focus:ring-1 focus:ring-[#D76428] outline-none"
                      value={k.topic}
                      onChange={(e) => oppdaterKanal(i, 'topic', e.target.value)}
                      placeholder="sensor/temperature"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-0.5">{t('Name')}</label>
                    <input
                      type="text"
                      className="block w-full rounded border border-gray-300 px-2 py-1 text-xs focus:ring-1 focus:ring-[#D76428] outline-none"
                      value={k.namn}
                      onChange={(e) => oppdaterKanal(i, 'namn', e.target.value)}
                      placeholder="Temperature"
                    />
                  </div>
                  <div className="flex items-end">
                    <button
                      className="px-2 py-1 text-xs text-red-600 hover:bg-red-50 rounded transition-colors"
                      onClick={() => fjernKanal(i)}
                      title={t('Remove')}
                    >
                      {t('Remove')}
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="block text-xs text-gray-500 mb-0.5">{t('Unit')}</label>
                    <input
                      type="text"
                      className="block w-full rounded border border-gray-300 px-2 py-1 text-xs focus:ring-1 focus:ring-[#D76428] outline-none"
                      value={k.enhet}
                      onChange={(e) => oppdaterKanal(i, 'enhet', e.target.value)}
                      placeholder="°C"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-0.5">{t('JSON path')}</label>
                    <input
                      type="text"
                      className="block w-full rounded border border-gray-300 px-2 py-1 text-xs focus:ring-1 focus:ring-[#D76428] outline-none"
                      value={k.json_sti}
                      onChange={(e) => oppdaterKanal(i, 'json_sti', e.target.value)}
                      placeholder="value"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-1">
                    <div>
                      <label className="block text-xs text-gray-500 mb-0.5">{t('Min')}</label>
                      <input
                        type="number"
                        className="block w-full rounded border border-gray-300 px-1.5 py-1 text-xs focus:ring-1 focus:ring-[#D76428] outline-none"
                        value={k.range_min}
                        onChange={(e) => oppdaterKanal(i, 'range_min', parseFloat(e.target.value) || 0)}
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-0.5">{t('Max')}</label>
                      <input
                        type="number"
                        className="block w-full rounded border border-gray-300 px-1.5 py-1 text-xs focus:ring-1 focus:ring-[#D76428] outline-none"
                        value={k.range_max}
                        onChange={(e) => oppdaterKanal(i, 'range_max', parseFloat(e.target.value) || 0)}
                      />
                    </div>
                  </div>
                </div>
                {status?.topics?.[k.topic] && (
                  <div className="mt-1 text-xs text-gray-500">
                    {t('Value')}:{' '}
                    <span className="font-mono font-semibold text-gray-800">
                      {status.topics[k.topic].verdi !== null
                        ? `${status.topics[k.topic].verdi} ${k.enhet}`
                        : '—'}
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {melding && (
        <div className="mb-3 p-2 bg-green-50 border border-green-200 rounded text-xs text-green-800">
          {melding}
        </div>
      )}
      {feil && (
        <div className="mb-3 p-2 bg-red-50 border border-red-200 rounded text-xs text-red-800">
          {feil}
        </div>
      )}

      <button
        className="w-full py-2 bg-[#D76428] text-white text-sm font-medium rounded-lg hover:bg-[#c05520] disabled:opacity-50 transition-colors"
        onClick={lagre}
        disabled={lagrar}
      >
        {lagrar ? t('Saving...') : t('Save MQTT config')}
      </button>
    </div>
  )
}
