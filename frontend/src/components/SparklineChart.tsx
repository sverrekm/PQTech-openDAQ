import { useEffect, useRef } from 'react'

interface Props {
  data: number[]
}

export default function SparklineChart({ data }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || data.length < 2) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Use clientWidth/clientHeight for responsive canvas sizing
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    const min = Math.min(...data)
    const max = Math.max(...data)
    const range = max - min || 1

    // Scale for high-DPI displays
    const dpi = window.devicePixelRatio
    canvas.width = w * dpi
    canvas.height = h * dpi
    ctx.scale(dpi, dpi)


    ctx.clearRect(0, 0, w, h)
    ctx.strokeStyle = '#D76428' // Original color
    ctx.lineWidth = 1.5
    ctx.beginPath()
    for (let i = 0; i < data.length; i++) {
      const x = (i / (data.length - 1)) * w
      const y = h - ((data[i] - min) / range) * (h - 4) - 2
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }, [data])

  return (
    <span className="inline-block w-20 h-6 align-middle">
      <canvas ref={canvasRef} className="w-full h-full" />
    </span>
  )
}
