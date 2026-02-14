interface Props {
  text: string
  visible?: boolean
}

export default function OutputTerminal({ text, visible = true }: Props) {
  if (!visible || !text) return null
  return <pre className="output-terminal">{text}</pre>
}
