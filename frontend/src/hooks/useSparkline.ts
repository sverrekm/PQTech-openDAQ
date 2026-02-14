import { useRef, useCallback } from 'react'

const MAX_POINTS = 30

export function useSparkline() {
  const dataRef = useRef<Map<number, number[]>>(new Map())

  const push = useCallback((idx: number, value: number) => {
    const map = dataRef.current
    if (!map.has(idx)) map.set(idx, [])
    const arr = map.get(idx)!
    arr.push(value)
    if (arr.length > MAX_POINTS) arr.shift()
  }, [])

  const draw = useCallback((idx: number, canvas: HTMLCanvasElement | null) => {
    if (!canvas) return
    const d = dataRef.current.get(idx)
    if (!d || d.length < 2) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const w = canvas.width
    const h = canvas.height
    const min = Math.min(...d)
    const max = Math.max(...d)
    const range = max - min || 1

    ctx.clearRect(0, 0, w, h)
    ctx.strokeStyle = '#D76428'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    for (let i = 0; i < d.length; i++) {
      const x = (i / (d.length - 1)) * w
      const y = h - ((d[i] - min) / range) * (h - 4) - 2
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }, [])

  const reset = useCallback(() => {
    dataRef.current.clear()
  }, [])

  return { push, draw, reset }
}
