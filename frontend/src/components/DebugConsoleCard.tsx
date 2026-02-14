import { useState } from 'react'
import { sendDebugKommando } from '../api/debug'
import OutputTerminal from './OutputTerminal'

export default function DebugConsoleCard() {
  const [kommando, setKommando] = useState('')
  const [poll, setPoll] = useState(false)
  const [output, setOutput] = useState('')
  const [busy, setBusy] = useState(false)

  const send = async () => {
    const cmd = kommando.trim()
    if (!cmd) return
    setBusy(true)
    try {
      const res = await sendDebugKommando(cmd, poll)
      if (res.feil) {
        setOutput(`Feil: ${res.feil}`)
      } else {
        setOutput(
          (res.svar ? `Svar: ${res.svar}\n` : '') +
          (res.hex ? `Hex: ${res.hex}` : '')
        )
      }
    } catch (e) {
      setOutput('Nettverksfeil: ' + String(e))
    }
    setBusy(false)
  }

  return (
    <div className="kort">
      <h2>Debug-konsoll</h2>
      <p style={{ color: '#6b6b6b', fontSize: '0.8rem', marginBottom: '0.5rem' }}>
        Send raa USB hex-kommandoar til SIRIUS.
      </p>
      <div className="debug-input-row">
        <input
          type="text"
          className="debug-input"
          placeholder="f.eks. 55aa0100..."
          value={kommando}
          onChange={e => setKommando(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send()}
        />
        <button className="btn btn-gronn" disabled={busy} onClick={send}>
          {busy ? 'Sender...' : 'Send'}
        </button>
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem', fontSize: '0.8rem', color: '#6b6b6b' }}>
        <input type="checkbox" checked={poll} onChange={e => setPoll(e.target.checked)} />
        Poll for svar (EP2)
      </label>
      <OutputTerminal text={output} />
    </div>
  )
}
