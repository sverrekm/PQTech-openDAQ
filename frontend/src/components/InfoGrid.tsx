interface InfoItem {
  label: string
  value: string | number
  color?: string
  small?: boolean
}

interface Props {
  items: InfoItem[]
}

export default function InfoGrid({ items }: Props) {
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-2">
      {items.map((item, i) => (
        <div className="bg-gray-50 rounded-md px-2.5 py-1.5" key={i}>
          <div className="text-[11px] text-gray-500">{item.label}</div>
          <div
            className={`mt-0.5 font-semibold text-gray-900 ${item.small ? 'text-xs' : 'text-sm'}`}
            style={{ color: item.color }}
          >
            {item.value || '-'}
          </div>
        </div>
      ))}
    </div>
  )
}
