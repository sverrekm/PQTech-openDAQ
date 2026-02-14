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

    const w = canvas.width
    const h = canvas.height
    const min = Math.min(...data)
    const max = Math.max(...data)
    const range = max - min || 1

    ctx.clearRect(0, 0, w, h)
    ctx.strokeStyle = '#D76428'
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
    <span className="sparkline-container">
      <canvas ref={canvasRef} width={80} height={24} />
    </span>
  )
}
