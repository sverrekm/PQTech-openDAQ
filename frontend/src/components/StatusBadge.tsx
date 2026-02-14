interface Props {
  ok: boolean
  label?: string
}

export default function StatusBadge({ ok, label }: Props) {
  return (
    <div className={`status-badge ${ok ? 'status-ok' : 'status-feil'}`}>
      <span className={`dot ${ok ? 'dot-gronn' : 'dot-rod'}`} />
      <span>{label ?? (ok ? 'Aktiv' : 'Stoppet')}</span>
    </div>
  )
}
