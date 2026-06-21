import { useState, useEffect, useCallback } from 'react'
import { oppdagNas, monterNas, avmonterNas, fetchNasStatus } from '../api/nas'
import type { NasShare, NasStatus } from '../api/nas'
import { fetchRaaFilKonfig, lagreRaaFilKonfig } from '../api/raaFil'
import type { RaaFilKonfig } from '../api/raaFil'
import { fetchHubLagerKonfig, lagreHubLagerKonfig, hubLagerCsvUrl } from '../api/hubLager'
import type { HubLagerKonfig } from '../api/hubLager'
import { useI18n } from '../i18n'

/** Samla lagrings-innstillingar: NAS-montering → rå-fil-arkiv → hub-database.
 *  Eitt kort med tydeleg flyt: kor måledata vert lagra. */
export default function StorageCard() {
  const { t } = useI18n()
  const felt = "w-full text-sm px-3 py-1.5 rounded-md border border-gray-300 focus:outline-none focus:ring-1 focus:ring-[#D76428] focus:border-[#D76428]"

  // ---- NAS ----
  const [nas, setNas] = useState<NasStatus | null>(null)
  const [vertar, setVertar] = useState<NasShare[]>([])
  const [valt, setValt] = useState('')
  const [brukar, setBrukar] = useState('')
  const [passord, setPassord] = useState('')
  const [nasBusy, setNasBusy] = useState<'' | 'skann' | 'monter'>('')
  const [nasMsg, setNasMsg] = useState<{ text: string; ok: boolean } | null>(null)

  // ---- Rå-fil-arkiv ----
  const [raa, setRaa] = useState<RaaFilKonfig>({ aktivert: false, katalog: '/data/nas/maalingar' })
  const [raaMsg, setRaaMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [raaBusy, setRaaBusy] = useState(false)

  // ---- Hub-database ----
  const [hub, setHub] = useState<HubLagerKonfig>({
    aktivert: false, db_sti: '/data/maalinger/hub_kanaldata.db',
    retensjon_dagar: 30, min_intervall_s: 1, maks_mb: 500,
  })
  const [hubMsg, setHubMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [hubBusy, setHubBusy] = useState(false)

  const last = useCallback(() => {
    fetchNasStatus().then(setNas).catch(() => {})
    fetchRaaFilKonfig().then(setRaa).catch(() => {})
    fetchHubLagerKonfig().then(setHub).catch(() => {})
  }, [])
  useEffect(() => {
    last()
    const id = setInterval(last, 12000)
    return () => clearInterval(id)
  }, [last])

  // ---- NAS-handlingar ----
  const skann = async () => {
    setNasBusy('skann'); setNasMsg(null); setVertar([])
    try {
      const r = await oppdagNas(brukar, passord)
      const v = (r.vertar || []).filter(h => h.shares.length > 0)
      setVertar(v)
      if (!v.length) setNasMsg({ text: t('No SMB shares found.'), ok: false })
    } catch (e) { setNasMsg({ text: String(e), ok: false }) }
    setNasBusy('')
  }
  const monter = async () => {
    const m = valt.match(/^\/\/([^/]+)\/(.+)$/)
    if (!m) { setNasMsg({ text: t('Select a share first.'), ok: false }); return }
    setNasBusy('monter'); setNasMsg(null)
    try {
      const r = await monterNas({ server: m[1], share: m[2], brukar, passord })
      setNasMsg({ text: r.melding, ok: r.suksess })
      // Auto-foreslå arkiv-katalog på NAS
      if (r.suksess && r.mountpunkt) setRaa(p => ({ ...p, katalog: `${r.mountpunkt}/maalingar` }))
      last()
    } catch (e) { setNasMsg({ text: String(e), ok: false }) }
    setNasBusy('')
  }
  const avmonter = async () => {
    setNasBusy('monter'); setNasMsg(null)
    try { const r = await avmonterNas(); setNasMsg({ text: r.melding, ok: r.suksess }); last() }
    catch (e) { setNasMsg({ text: String(e), ok: false }) }
    setNasBusy('')
  }

  // ---- Rå-arkiv lagre ----
  const lagreRaa = async () => {
    setRaaBusy(true); setRaaMsg(null)
    try { await lagreRaaFilKonfig({ aktivert: raa.aktivert, katalog: raa.katalog }); setRaaMsg({ text: t('Saved.'), ok: true }); last() }
    catch (e) { setRaaMsg({ text: String(e), ok: false }) }
    setRaaBusy(false)
  }

  // ---- Hub-DB lagre ----
  const lagreHub = async () => {
    setHubBusy(true); setHubMsg(null)
    try {
      await lagreHubLagerKonfig({
        aktivert: hub.aktivert, db_sti: hub.db_sti, retensjon_dagar: hub.retensjon_dagar,
        min_intervall_s: hub.min_intervall_s, maks_mb: hub.maks_mb,
      })
      setHubMsg({ text: t('Saved.'), ok: true }); last()
    } catch (e) { setHubMsg({ text: String(e), ok: false }) }
    setHubBusy(false)
  }

  const Msg = ({ m }: { m: { text: string; ok: boolean } | null }) => m ? (
    <div className={`mt-2 px-3 py-2 rounded-lg text-sm ${m.ok ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>{m.text}</div>
  ) : null

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">{t('Storage')}</h2>
      <p className="text-sm text-gray-500 -mt-1 mb-4 leading-snug">{t('Where measurement data is stored. Mount a NAS for capacity, archive raw files to it, and optionally keep a queryable database on the hub.')}</p>

      {/* ───────── 1. NAS ───────── */}
      <div className="border-t border-gray-100 pt-3">
        <h3 className="text-sm font-semibold text-gray-700 mb-1">1 · {t('Network storage (NAS)')}</h3>
        {nas?.montert ? (
          <div className="mb-2 px-3 py-2 rounded-lg bg-green-50 text-green-800 text-sm flex items-center justify-between">
            <span>✓ //{nas.server}/{nas.share} → {nas.mountpunkt}
              {nas.total_gb != null && <> · {nas.ledig_gb}/{nas.total_gb} GB {t('free')}</>}</span>
            <button onClick={avmonter} disabled={nasBusy !== ''} className="text-red-600 hover:underline text-xs ml-2">{t('Unmount')}</button>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2 mb-2">
              <input className={`${felt} flex-1 min-w-[120px]`} placeholder={t('Username (optional)')} value={brukar} onChange={e => setBrukar(e.target.value)} autoComplete="off" />
              <input className={`${felt} flex-1 min-w-[120px]`} type="password" placeholder={t('Password')} value={passord} onChange={e => setPassord(e.target.value)} autoComplete="new-password" />
            </div>
            <button onClick={skann} disabled={nasBusy !== ''} className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50">
              {nasBusy === 'skann' ? t('Scanning...') : t('Scan for NAS')}
            </button>
            {vertar.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2 items-center">
                <select className={`${felt} flex-1 min-w-[200px]`} value={valt} onChange={e => setValt(e.target.value)}>
                  <option value="">{t('— select —')}</option>
                  {vertar.flatMap(h => h.shares.map(s => {
                    const unc = `//${h.ip}/${s}`
                    return <option key={unc} value={unc}>{unc}{h.hostname ? ` (${h.hostname})` : ''}</option>
                  }))}
                </select>
                <button onClick={monter} disabled={nasBusy !== '' || !valt} className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50">
                  {nasBusy === 'monter' ? t('Mounting...') : t('Mount selected')}
                </button>
              </div>
            )}
          </>
        )}
        <Msg m={nasMsg} />
      </div>

      {/* ───────── 2. Rå-fil-arkiv ───────── */}
      <div className="border-t border-gray-100 pt-3 mt-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-1">2 · {t('Raw file archive')}</h3>
        <p className="text-xs text-gray-500 mb-2">{t('Bulk measurement files as CSV — point this at the NAS folder so the container does not fill up.')}</p>
        <label className="flex items-center gap-2 mb-2 text-sm text-gray-700">
          <input type="checkbox" checked={raa.aktivert} onChange={e => setRaa(p => ({ ...p, aktivert: e.target.checked }))} className="accent-[#D76428]" />
          {t('Enabled')}
          {raa.skrivbar != null && (
            <span className={`ml-2 text-xs ${raa.skrivbar ? 'text-green-600' : 'text-red-500'}`}>
              {raa.skrivbar ? `✓ ${t('Writable')}` : `✗ ${t('Writable')}`}
            </span>
          )}
        </label>
        <input className={felt} value={raa.katalog} onChange={e => setRaa(p => ({ ...p, katalog: e.target.value }))} placeholder="/data/nas/maalingar" />
        <div className="flex items-center gap-3 mt-2">
          <button onClick={lagreRaa} disabled={raaBusy} className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50">
            {raaBusy ? t('Saving...') : t('Save')}
          </button>
          <span className="text-xs text-gray-400">{(raa.skrive_totalt ?? 0).toLocaleString()} {t('Rows written')}</span>
        </div>
        <Msg m={raaMsg} />
      </div>

      {/* ───────── 3. Hub-database ───────── */}
      <div className="border-t border-gray-100 pt-3 mt-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-1">3 · {t('Hub database (channel data)')}</h3>
        <p className="text-xs text-gray-500 mb-2">{t('Optional queryable copy on the hub. Keep on local disk (not NAS — SQLite corrupts over network). Bounded by max size.')}</p>
        <label className="flex items-center gap-2 mb-2 text-sm text-gray-700">
          <input type="checkbox" checked={hub.aktivert} onChange={e => setHub(p => ({ ...p, aktivert: e.target.checked }))} className="accent-[#D76428]" />
          {t('Enabled')}
        </label>
        <input className={`${felt} mb-2`} value={hub.db_sti} onChange={e => setHub(p => ({ ...p, db_sti: e.target.value }))} placeholder="/data/maalinger/hub_kanaldata.db" />
        <div className="flex flex-wrap gap-2">
          <div className="w-32">
            <label className="block text-xs text-gray-500 mb-1">{t('Retention (days)')}</label>
            <input className={felt} type="number" min={0} value={hub.retensjon_dagar} onChange={e => setHub(p => ({ ...p, retensjon_dagar: Number(e.target.value) }))} />
          </div>
          <div className="w-36">
            <label className="block text-xs text-gray-500 mb-1">{t('Min interval (s)')}</label>
            <input className={felt} type="number" min={0} step={0.1} value={hub.min_intervall_s} onChange={e => setHub(p => ({ ...p, min_intervall_s: Number(e.target.value) }))} />
          </div>
          <div className="w-32">
            <label className="block text-xs text-gray-500 mb-1">{t('Max size (MB)')}</label>
            <input className={felt} type="number" min={0} value={hub.maks_mb} onChange={e => setHub(p => ({ ...p, maks_mb: Number(e.target.value) }))} />
          </div>
        </div>
        <div className="flex items-center gap-3 mt-2">
          <button onClick={lagreHub} disabled={hubBusy} className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-1.5 px-3 rounded-lg text-sm disabled:opacity-50">
            {hubBusy ? t('Saving...') : t('Save')}
          </button>
          <a href={hubLagerCsvUrl()} className="text-[#D76428] hover:underline text-sm">{t('Download CSV')}</a>
          <span className="text-xs text-gray-400">{(hub.rader ?? 0).toLocaleString()} {t('Rows')} · {(hub.storleik_mb ?? 0)} MB</span>
        </div>
        <Msg m={hubMsg} />
      </div>
    </div>
  )
}
