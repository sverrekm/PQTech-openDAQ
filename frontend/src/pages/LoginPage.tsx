import { useState } from 'react'
import { loggInn } from '../api/auth'

interface Props {
  onLogin: () => void
}

export default function LoginPage({ onLogin }: Props) {
  const [brukarnavn, setBrukarnavn] = useState('')
  const [passord, setPassord] = useState('')
  const [feil, setFeil] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setFeil('')
    setLoading(true)
    try {
      const res = await loggInn(brukarnavn, passord)
      if (res.suksess) {
        onLogin()
      } else {
        setFeil(res.melding || 'Feil brukarnavn eller passord')
      }
    } catch {
      setFeil('Kunne ikkje kontakte serveren')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#111] flex items-center justify-center">
      <div className="bg-[#1a1a1a] border border-gray-700 rounded-lg p-8 w-full max-w-sm shadow-lg">
        <h1 className="text-2xl font-semibold text-white text-center mb-6">
          <span className="text-[#D76428]">PQTech</span>-openDAQ
        </h1>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Brukarnavn</label>
            <input
              type="text"
              value={brukarnavn}
              onChange={e => setBrukarnavn(e.target.value)}
              className="w-full bg-[#222] border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-[#D76428]"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm text-gray-400 mb-1">Passord</label>
            <input
              type="password"
              value={passord}
              onChange={e => setPassord(e.target.value)}
              className="w-full bg-[#222] border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-[#D76428]"
            />
          </div>

          {feil && (
            <p className="text-red-400 text-sm">{feil}</p>
          )}

          <button
            type="submit"
            disabled={loading || !brukarnavn || !passord}
            className="w-full bg-[#D76428] hover:bg-[#c05520] disabled:opacity-50 text-white font-medium py-2 rounded transition-colors"
          >
            {loading ? 'Loggar inn...' : 'Logg inn'}
          </button>
        </form>
      </div>
    </div>
  )
}
