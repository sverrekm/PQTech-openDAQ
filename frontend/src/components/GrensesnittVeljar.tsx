import { useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchGrensesnitt } from '../api/vert'
import type { VertGrensesnitt } from '../api/vert'

/**
 * Nedtrekksmeny over vertens nettverksgrensesnitt.
 *
 * Namna varierer mellom nodane — `eth0` på nokre, `end0` på andre — og å
 * skrive dei for hand er ei feilkjelde vi ikkje treng. Klarer vi ikkje å
 * lese lista, fell vi tilbake til eit fritekstfelt i staden for å blokkere.
 */
export default function GrensesnittVeljar({
  verdi, onEndra, className,
}: {
  verdi: string
  onEndra: (dev: string) => void
  className?: string
}) {
  const fetcher = useCallback(() => fetchGrensesnitt(), [])
  const { data } = usePolling<{ grensesnitt: VertGrensesnitt[] }>(fetcher, 60000)
  const liste = data?.grensesnitt ?? []

  const felt = className ??
    'block w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:ring-2 focus:ring-[#D76428] outline-none'

  if (liste.length === 0) {
    return (
      <input type="text" className={felt} value={verdi}
        onChange={(e) => onEndra(e.target.value)} placeholder="wlan0" />
    )
  }

  // Ei verdi som ikkje finst i lista (t.d. lagra frå før) skal ikkje forsvinne
  const ukjend = verdi && !liste.some((g) => g.dev === verdi)

  return (
    <select className={felt} value={verdi} onChange={(e) => onEndra(e.target.value)}>
      {ukjend && <option value={verdi}>{verdi}</option>}
      {liste.map((g) => (
        <option key={g.dev} value={g.dev}>
          {g.dev}
          {g.adresse ? ` — ${g.adresse}` : g.oppe ? ' — oppe' : ' — nede'}
          {g.type === 'wifi' ? ' (wifi)' : ''}
        </option>
      ))}
    </select>
  )
}
