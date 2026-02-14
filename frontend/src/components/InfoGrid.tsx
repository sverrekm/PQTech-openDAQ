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
    <div className="info-grid">
      {items.map((item, i) => (
        <div className="info-boks" key={i}>
          <div className="label">{item.label}</div>
          <div
            className="verdi"
            style={{
              color: item.color,
              fontSize: item.small ? '0.75rem' : undefined,
            }}
          >
            {item.value || '-'}
          </div>
        </div>
      ))}
    </div>
  )
}
