interface Props {
  text: string
  className?: string // Allow passing additional Tailwind classes
}

export default function CopyableCommand({ text, className }: Props) {
  const copy = () => {
    navigator.clipboard.writeText(text)
  }

  return (
    <div className={`bg-gray-50 border border-gray-200 rounded-lg p-3 font-mono text-sm text-orange-700 flex items-center justify-between gap-3 ${className}`}>
      <code className="flex-1 break-all">{text}</code>
      <button className="bg-gray-200 text-gray-800 border-none px-2.5 py-1.5 rounded-md cursor-pointer text-xs whitespace-nowrap hover:bg-gray-300" onClick={copy}>Kopier</button>
    </div>
  )
}
