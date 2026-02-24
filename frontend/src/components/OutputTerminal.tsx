interface Props {
  text: string
  visible?: boolean
}

export default function OutputTerminal({ text, visible = true }: Props) {
  if (!visible || !text) return null
  return <pre className="bg-gray-800 border border-gray-700 rounded-lg p-3 mt-3 text-xs text-orange-400 max-h-96 overflow-y-auto whitespace-pre-wrap font-mono">{text}</pre>
}
