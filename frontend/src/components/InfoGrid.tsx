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
    <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
      {items.map((item, i) => (
        <div className="bg-gray-50 rounded-lg p-3" key={i}>
          <div className="text-xs text-gray-500">{item.label}</div>
          <div
            className={`mt-1 font-semibold text-gray-900 ${item.small ? 'text-xs' : 'text-lg'}`}
            style={{ color: item.color }}
          >
            {item.value || '-'}
          </div>
        </div>
      ))}
    </div>
  )
}
