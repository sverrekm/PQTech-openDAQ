import { useEffect, useRef } from 'react'

interface Props {
  value: number | null | undefined
  color?: string
  maxPoints?: number
}

/**
 * Live-sparkline som held eit eige verdi-buffer og teiknar til <canvas>.
 * Same teiknemønster som ChannelPage, men gjenbrukbart for hub-/MQTT-kanalar
 * som berre har éin verdi (ingen RMS/topp/snitt).
 */
export default function LiveSparkline({ value, color = '#D76428', maxPoints = 60 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const buf = useRef<number[]>([])

  useEffect(() => {
    if (value !== null && value !== undefined) {
      const n = typeof value === 'number' ? value : parseFloat(String(value))
      if (!isNaN(n)) {
        buf.current.push(n)
        if (buf.current.length > maxPoints) buf.current.shift()
      }
    }

    const canvas = canvasRef.current
    const data = buf.current
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
    ctx.strokeStyle = color
    ctx.lineWidth = 2
    ctx.beginPath()
    for (let i = 0; i < data.length; i++) {
      const x = (i / (data.length - 1)) * w
      const y = h - ((data[i] - min) / range) * (h - 8) - 4
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }, [value, color, maxPoints])

  return (
    <div className="mt-4 h-24 w-full">
      <canvas ref={canvasRef} className="w-full h-full block" />
    </div>
  )
}
