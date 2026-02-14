interface Props {
  text: string
  style?: React.CSSProperties
}

export default function CopyableCommand({ text, style }: Props) {
  const copy = () => {
    navigator.clipboard.writeText(text)
  }

  return (
    <div className="cmd-boks" style={style}>
      <code>{text}</code>
      <button className="btn-kopier" onClick={copy}>Kopier</button>
    </div>
  )
}
