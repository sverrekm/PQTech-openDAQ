import { useState, useEffect, useCallback } from 'react'
import { oppdagNas, monterNas, avmonterNas, fetchNasStatus } from '../api/nas'
import type { NasShare, NasStatus } from '../api/nas'
import { useI18n } from '../i18n'

/** Oppdag og monter nettverkslagring (SMB/CIFS) frå GUI. Hubben skannar
 *  LAN-et, listar delingar, og monterer ei valt deling i containeren. */
export default function NasCard() {
  const { t } = useI18n()
  const [status, setStatus] = useState<NasStatus | null>(null)
  const [vertar, setVertar] = useState<NasShare[]>([])
  const [valt, setValt] = useState('')           // "//ip/share"
  const [brukar, setBrukar] = useState('')
  const [passord, setPassord] = useState('')
  const [busy, setBusy] = useState<'' | 'skann' | 'monter'>('')
  const [melding, setMelding] = useState<{ text: string; ok: boolean } | null>(null)

  const lastStatus = useCallback(() => { fetchNasStatus().then(setStatus).catch(() => {}) }, [])
  useEffect(() => {
    lastStatus()
    const id = setInterval(lastStatus, 15000)
    return () => clearInterval(id)
  }, [lastStatus])

  const skann = async () => {
    setBusy('skann'); setMelding(null); setVertar([])
    try {
      const r = await oppdagNas(brukar, passord)
      const v = (r.vertar || []).filter(h => h.shares.length > 0)
      setVertar(v)
      if (v.length === 0) setMelding({ text: t('No SMB shares found.'), ok: false })
    } catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy('')
  }

  const monter = async () => {
    const m = valt.match(/^\/\/([^/]+)\/(.+)$/)
    if (!m) { setMelding({ text: t('Select a share first.'), ok: false }); return }
    setBusy('monter'); setMelding(null)
    try {
      const r = await monterNas({ server: m[1], share: m[2], brukar, passord })
      setMelding({ text: r.melding, ok: r.suksess })
      lastStatus()
    } catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy('')
  }

  const avmonter = async () => {
    setBusy('monter'); setMelding(null)
    try { const r = await avmonterNas(); setMelding({ text: r.melding, ok: r.suksess }); lastStatus() }
    catch (e) { setMelding({ text: String(e), ok: false }) }
    setBusy('')
  }

  const felt = "w-full text-sm px-3 py-1.5 rounded-md border border-gray-300 focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">{t('Network storage (NAS)')}</h2>
      <p className="text-sm text-gray-500 -mt-1 mb-4 leading-snug">{t('Discover SMB/CIFS shares on the network and mount one as the storage location for raw measurement files.')}</p>

      {/* Noverande status */}
      {status?.montert ? (
        <div className="mb-3 px-3 py-2 rounded-lg bg-green-50 text-green-800 text-sm flex items-center justify-between">
          <span>✓ {t('Mounted')}: //{status.server}/{status.share} → {status.mountpunkt}
            {status.total_gb != null && <> · {status.ledig_gb}/{status.total_gb} GB {t('free')}</>}
          </span>
          <button onClick={avmonter} disabled={busy !== ''} className="text-red-600 hover:underline text-xs ml-2">{t('Unmount')}</button>
        </div>
      ) : (
        <div className="mb-3 px-3 py-2 rounded-lg bg-gray-50 text-gray-600 text-sm">{t('No NAS mounted.')}</div>
      )}

      {/* Credentials (valfri for skann, kravd for verna delingar) */}
      <div className="flex flex-wrap gap-2 mb-2">
        <div className="flex-1 min-w-[140px]">
          <label className="block text-xs text-gray-500 mb-1">{t('Username (optional)')}</label>
          <input className={felt} value={brukar} onChange={e => setBrukar(e.target.value)} autoComplete="off" />
        </div>
        <div className="flex-1 min-w-[140px]">
          <label className="block text-xs text-gray-500 mb-1">{t('Password')}</label>
          <input className={felt} type="password" value={passord} onChange={e => setPassord(e.target.value)} autoComplete="new-password" />
        </div>
      </div>

      <button onClick={skann} disabled={busy !== ''} className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50 mb-3">
        {busy === 'skann' ? t('Scanning...') : t('Scan for NAS')}
      </button>

      {/* Funne delingar */}
      {vertar.length > 0 && (
        <div className="mb-3">
          <label className="block text-xs text-gray-500 mb-1">{t('Found shares')}</label>
          <select className={felt} value={valt} onChange={e => setValt(e.target.value)}>
            <option value="">{t('— select —')}</option>
            {vertar.flatMap(h => h.shares.map(s => {
              const unc = `//${h.ip}/${s}`
              return <option key={unc} value={unc}>{unc}{h.hostname ? ` (${h.hostname})` : ''}</option>
            }))}
          </select>
          <button onClick={monter} disabled={busy !== '' || !valt} className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50 mt-2">
            {busy === 'monter' ? t('Mounting...') : t('Mount selected')}
          </button>
        </div>
      )}

      {melding && (
        <div className={`mt-2 px-3 py-2 rounded-lg text-sm ${melding.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
          {melding.text}
        </div>
      )}
      {status?.montert && (
        <p className="text-[11px] text-gray-400 mt-3">{t('Set the raw file archive folder to')} <code>{status.mountpunkt}/maalingar</code></p>
      )}
    </div>
  )
}
