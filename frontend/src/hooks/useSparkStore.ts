import { useRef, useEffect } from 'react'

/**
 * Vedvarande sparkline-historikk for dashbord-målarane.
 *
 * Målar-grafane er berre ein visuell «er det liv på kanalen»-indikator, men
 * historikken låg tidlegare berre i minnet (useRef) og forsvann ved kvar
 * side-refresh. Denne hooken lastar den siste historikken frå localStorage
 * ved montering og lagrar han att (strupt) så grafane og verdiane står att
 * over ein refresh. Berre ein per-nettlesar-bekvemmelegheit — feilar den
 * (privat modus, blokkert lagring) held vi fram med tom historikk.
 */

const MAKS_PUNKT = 30          // samsvarar med grafane sin lengde
const LAGRE_INTERVALL_MS = 2000

function last(nokkel: string): Map<string, number[]> {
  try {
    const raw = localStorage.getItem(nokkel)
    if (!raw) return new Map()
    const obj = JSON.parse(raw) as Record<string, number[]>
    const m = new Map<string, number[]>()
    for (const [k, v] of Object.entries(obj)) {
      if (Array.isArray(v)) m.set(k, v.filter(n => typeof n === 'number').slice(-MAKS_PUNKT))
    }
    return m
  } catch {
    return new Map()
  }
}

function lagre(nokkel: string, m: Map<string, number[]>) {
  try {
    localStorage.setItem(nokkel, JSON.stringify(Object.fromEntries(m)))
  } catch {
    /* privat modus / full disk — ignorer, dette er berre pynt */
  }
}

export function useSparkStore(nokkel: string) {
  const ref = useRef<Map<string, number[]>>()
  if (ref.current === undefined) ref.current = last(nokkel)
  const sisteLagra = useRef(0)

  // Lagre etter kvar render, men ikkje oftare enn LAGRE_INTERVALL_MS.
  useEffect(() => {
    const no = Date.now()
    if (no - sisteLagra.current < LAGRE_INTERVALL_MS) return
    sisteLagra.current = no
    lagre(nokkel, ref.current!)
  })

  const spark = ref.current!

  /** Legg til eit punkt og returner (den avkorta) serien. */
  const push = (key: string, v: number): number[] => {
    if (!isNaN(v)) {
      const arr = spark.get(key) || []
      arr.push(v)
      if (arr.length > MAKS_PUNKT) arr.shift()
      spark.set(key, arr)
    }
    return spark.get(key) || []
  }

  /** Siste lagra punkt for ein kanal (fallback-verdi før første poll). */
  const siste = (key: string): number | undefined => {
    const arr = spark.get(key)
    return arr && arr.length ? arr[arr.length - 1] : undefined
  }

  return { spark, push, siste }
}
