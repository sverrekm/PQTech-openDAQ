import StatusBadge from './StatusBadge'

interface Props {
  serverOk: boolean
  loading: boolean
}

export default function Header({ serverOk, loading }: Props) {
  return (
    <div className="bg-[#1a1a1a] border-b-[3px] border-b-[#D76428] p-4 md:px-6 flex items-center justify-between">
      <h1 className="text-xl font-semibold text-white">
        <span className="text-[#D76428]">PQTech</span>-openDAQ
      </h1>
      <StatusBadge ok={serverOk} loading={loading} />
    </div>
  )
}
