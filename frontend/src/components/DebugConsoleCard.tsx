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
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">Debug-konsoll</h2>
      <p className="text-gray-500 text-xs mb-2">
        Send rå USB hex-kommandoar til SIRIUS.
      </p>
      <div className="flex gap-2">
        <input
          type="text"
          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg p-2 font-mono text-sm text-orange-400 outline-none focus:border-[#D76428]"
          placeholder="f.eks. 55aa0100..."
          value={kommando}
          onChange={e => setKommando(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send()}
        />
        <button className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={busy} onClick={send}>
          {busy ? 'Sender...' : 'Send'}
        </button>
      </div>
      <label className="flex items-center gap-2 mt-2 text-sm text-gray-500">
        <input type="checkbox" checked={poll} onChange={e => setPoll(e.target.checked)} className="h-4 w-4 accent-[#D76428] rounded focus:ring-[#D76428]" />
        Poll for svar (EP2)
      </label>
      <OutputTerminal text={output} />
    </div>
  )
}
