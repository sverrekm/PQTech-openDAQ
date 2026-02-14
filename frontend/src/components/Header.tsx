import StatusBadge from './StatusBadge'

interface Props {
  serverOk: boolean
}

export default function Header({ serverOk }: Props) {
  return (
    <div className="header">
      <h1><span>PQTech</span>-openDAQ</h1>
      <StatusBadge ok={serverOk} />
    </div>
  )
}
