import { useEffect, useRef } from 'react'
import type { KanalKonfig, KanalLive } from '../api/types'

interface Props {
  index: number
  kanalar: KanalKonfig[]
  liveData: KanalLive | null
  onBack?: () => void
}

export default function ChannelPage({ index, kanalar, liveData, onBack }: Props) {
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

  // Update sparkline buffer with instantaneous values (for trend visualization)
  useEffect(() => {
    if (cv !== null) {
      const numVal = typeof cv.value === 'number' ? cv.value : parseFloat(String(cv.value))
      if (!isNaN(numVal)) {
        sparkBuf.current.push(numVal)
        if (sparkBuf.current.length > 60) sparkBuf.current.shift()
      }
    }
  }, [cv?.value])

  // Draw sparkline
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
    return <div className="kort">Kanal ikkje funnen.</div>
  }

  const typeLabel = kanal.type === 'voltage' ? 'Spenning' : kanal.type === 'current' ? 'Straum' : kanal.type

  // Use backend-computed stats (from full 20kHz ADC batches), not sparkline buffer
  const hasBackendStats = cv && 'rms' in cv && cv.rms !== undefined
  const stats = hasBackendStats
    ? { rms: cv.rms!, topp: cv.topp!, snitt: cv.snitt! }
    : null

  return (
    <>
      <div className="kort">
        <div className="kanal-detalj-header">
          {onBack && (
            <button className="kanal-detalj-tilbake" onClick={onBack}>
              &#8592; Tilbake
            </button>
          )}
          <div className="kanal-detalj-tittel">
            {kanal.namn} — {typeLabel}
            <span className="kanal-eining">{kanal.enhet}</span>
          </div>
        </div>

        <div className="kanal-detalj-live">
          <div className="kanal-detalj-verdi" style={{ color: cv?.color || '#6b6b6b' }}>
            {cv ? Number(cv.value).toFixed(2) : '—'}
            <span className="verdi-eining">{kanal.enhet}</span>
          </div>
          {stats && (
            <div className="kanal-detalj-stats">
              <div className="stat-rad">RMS: <strong>{stats.rms.toFixed(2)} {kanal.enhet}</strong></div>
              <div className="stat-rad">Topp: <strong>{stats.topp.toFixed(2)} {kanal.enhet}</strong></div>
              <div className="stat-rad">Snitt: <strong>{stats.snitt.toFixed(2)} {kanal.enhet}</strong></div>
            </div>
          )}
        </div>

        <div className="kanal-detalj-sparkline">
          <canvas ref={canvasRef} />
        </div>
      </div>

      <div className="kort">
        <h2>Konfigurasjon</h2>
        <div className="kanal-detalj-konfig">
          <div className="info-boks">
            <div className="label">Range</div>
            <div className="verdi">{kanal.range_min} / {kanal.range_max}</div>
          </div>
          <div className="info-boks">
            <div className="label">Eining</div>
            <div className="verdi">{kanal.enhet}</div>
          </div>
          <div className="info-boks">
            <div className="label">Type</div>
            <div className="verdi">{typeLabel}</div>
          </div>
          <div className="info-boks">
            <div className="label">Sample rate</div>
            <div className="verdi">{kanal.sample_rate} Hz</div>
          </div>
        </div>
      </div>

      <div className="kort">
        <h2>Innstillingar</h2>
        <div className="kanal-detalj-plassholder">
          Per-kanal innstillingar — bruk Innstillingar-sida for å endre kanalkonfigurasjon.
        </div>
      </div>
    </>
  )
}
