import { useEffect, useRef } from 'react'
import type { KanalKonfig, KanalLive } from '../api/types'
import { useI18n } from '../i18n'

interface Props {
  index: number
  kanalar: KanalKonfig[]
  liveData: KanalLive | null
  onBack?: () => void
}

export default function ChannelPage({ index, kanalar, liveData, onBack }: Props) {
  const { t } = useI18n()
  const kanal = kanalar.find(k => k.indeks === index)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sparkBuf = useRef<number[]>([])

  const getChannelData = () => {
    if (!liveData) return null
    const key = `kanal_${index}`
    const odaq = liveData.opendaq?.[key]
    const drv = liveData.driver?.[key]

    if (odaq && odaq.kjelde === 'sirius' && odaq.siste !== undefined) {
      return {
        value: odaq.siste,
        source: 'Sirius',
        color: '#10b981',
        rms: odaq.rms,
        topp: odaq.topp,
        snitt: odaq.snitt,
      }
    }
    if (odaq && odaq.siste !== undefined) {
      return {
        value: odaq.siste,
        source: 'OpenDAQ',
        color: '#D76428',
        rms: odaq.rms,
        topp: odaq.topp,
        snitt: odaq.snitt,
      }
    }
    if (drv && drv.siste !== null && drv.siste !== undefined) {
      return { value: drv.siste, source: 'Driver', color: '#3b82f6' }
    }
    return null
  }

  const cv = getChannelData()

  useEffect(() => {
    if (cv !== null) {
      const numVal = typeof cv.value === 'number' ? cv.value : parseFloat(String(cv.value))
      if (!isNaN(numVal)) {
        sparkBuf.current.push(numVal)
        if (sparkBuf.current.length > 60) sparkBuf.current.shift()
      }
    }
  }, [cv?.value])

  useEffect(() => {
    const canvas = canvasRef.current
    const data = sparkBuf.current
    if (!canvas || data.length < 2) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const rect = canvas.getBoundingClientRect()
    canvas.width = rect.width * window.devicePixelRatio
    canvas.height = rect.height * window.devicePixelRatio
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio)

    const w = rect.width
    const h = rect.height
    const min = Math.min(...data)
    const max = Math.max(...data)
    const range = max - min || 1

    ctx.clearRect(0, 0, w, h)
    ctx.strokeStyle = '#D76428'
    ctx.lineWidth = 2
    ctx.beginPath()
    for (let i = 0; i < data.length; i++) {
      const x = (i / (data.length - 1)) * w
      const y = h - ((data[i] - min) / range) * (h - 8) - 4
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }, [cv?.value])

  if (!kanal) {
    return <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">{t('Channel not found.')}</div>
  }

  const typeLabel = kanal.type === 'voltage' ? t('Voltage') : kanal.type === 'current' ? t('Current') : kanal.type

  const hasBackendStats = cv && 'rms' in cv && cv.rms !== undefined
  const stats = hasBackendStats
    ? { rms: cv.rms!, topp: cv.topp!, snitt: cv.snitt! }
    : null

  return (
    <>
      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <div className="flex items-center gap-3 mb-4">
          {onBack && (
            <button className="text-sm text-gray-500 hover:text-[#D76428] cursor-pointer bg-transparent border-none" onClick={onBack}>
              &#8592; {t('Back')}
            </button>
          )}
          <div className="text-lg font-semibold text-gray-900">
            {kanal.namn} — {typeLabel}
            <span className="text-sm text-gray-500 font-normal ml-1">{kanal.enhet}</span>
          </div>
        </div>

        <div className="flex items-baseline gap-6 mb-4">
          <div className="text-4xl font-bold tabular-nums" style={{ color: cv?.color || '#6b6b6b' }}>
            {cv ? Number(cv.value).toFixed(2) : '—'}
            <span className="text-lg font-normal text-gray-500 ml-1">{kanal.enhet}</span>
          </div>
          {stats && (
            <div className="flex gap-4 text-sm text-gray-600">
              <div>RMS: <strong>{stats.rms.toFixed(2)} {kanal.enhet}</strong></div>
              <div>{t('Peak:')}<strong> {stats.topp.toFixed(2)} {kanal.enhet}</strong></div>
              <div>{t('Average:')}<strong> {stats.snitt.toFixed(2)} {kanal.enhet}</strong></div>
            </div>
          )}
        </div>

        <div className="mt-4 h-24 w-full">
          <canvas ref={canvasRef} className="w-full h-full block" />
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2>{t('Configuration')}</h2>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-xs text-gray-500">{t('Range')}</div>
            <div className="mt-1 font-semibold text-gray-900">{kanal.range_min} / {kanal.range_max}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-xs text-gray-500">{t('Unit')}</div>
            <div className="mt-1 font-semibold text-gray-900">{kanal.enhet}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-xs text-gray-500">{t('Type')}</div>
            <div className="mt-1 font-semibold text-gray-900">{typeLabel}</div>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="text-xs text-gray-500">{t('Sample rate')}</div>
            <div className="mt-1 font-semibold text-gray-900">{kanal.sample_rate} Hz</div>
          </div>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
        <h2>{t('Settings')}</h2>
        <div className="text-sm text-gray-500">
          {t('Channel settings — use the Settings page to change channel configuration.')}
        </div>
      </div>
    </>
  )
}
