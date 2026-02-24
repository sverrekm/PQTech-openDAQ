interface Props {
  ok: boolean
  label?: string
  loading?: boolean
}

export default function StatusBadge({ ok, label, loading = false }: Props) {
  if (loading) {
    return (
      <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
        <span className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" />
        <span>Laster...</span>
      </div>
    )
  }

  return (
    <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium ${ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
      <span className={`w-2 h-2 rounded-full ${ok ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
      <span>{label ?? (ok ? 'Server aktiv' : 'Server stoppa')}</span>
    </div>
  )
}
