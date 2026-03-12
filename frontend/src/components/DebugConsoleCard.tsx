import { useState } from 'react'
import { sendDebugKommando } from '../api/debug'
import OutputTerminal from './OutputTerminal'
import { useI18n } from '../i18n'

export default function DebugConsoleCard() {
  const { t } = useI18n()
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
        setOutput(`${t('Error')}: ${res.feil}`)
      } else {
        setOutput(
          (res.svar ? `${res.svar}\n` : '') +
          (res.hex ? `Hex: ${res.hex}` : '')
        )
      }
    } catch (e) {
      setOutput(t('Network error: ') + String(e))
    }
    setBusy(false)
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">{t('Debug console')}</h2>
      <p className="text-gray-500 text-xs mb-2">
        {t('Send raw USB hex commands to SIRIUS.')}
      </p>
      <div className="flex gap-2">
        <input
          type="text"
          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg p-2 font-mono text-sm text-orange-400 outline-none focus:border-[#D76428]"
          placeholder="e.g. 55aa0100..."
          value={kommando}
          onChange={e => setKommando(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send()}
        />
        <button className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={busy} onClick={send}>
          {busy ? t('Sending...') : t('Send')}
        </button>
      </div>
      <label className="flex items-center gap-2 mt-2 text-sm text-gray-500">
        <input type="checkbox" checked={poll} onChange={e => setPoll(e.target.checked)} className="h-4 w-4 accent-[#D76428] rounded focus:ring-[#D76428]" />
        {t('Poll for response (EP2)')}
      </label>
      <OutputTerminal text={output} />
    </div>
  )
}
