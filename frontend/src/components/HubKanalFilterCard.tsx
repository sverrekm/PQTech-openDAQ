import { useCallback, useMemo, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { fetchHubKanalar } from '../api/hub'
import type { HubKanal } from '../api/types'
import {
  hentSynlegeKanalar, lagreSynlegeKanalar,
  lagreNodeRekkefolgje, sorterEtterRekkefolgje,
} from '../pages/HubPage'
import Panel from './ui/Panel'
import { useI18n } from '../i18n'

/**
 * Vel kva kanalar — og frå kva nodar — som visast på hub-dashbordet.
 *
 * Gruppert per node, med av/på for heile noden eller enkeltkanalar. Skriv
 * til same localStorage-utval (`hub_synlege_kanalar`) som resten av
 * dashbordet respekterer, og sender ei hending so ChannelLiveCard
 * oppdaterer straks. null-utval = alt synleg (standard).
 */
export default function HubKanalFilterCard() {
  const { t } = useI18n()
  const fetcher = useCallback(() => fetchHubKanalar(), [])
  const { data } = usePolling<{ kanalar: HubKanal[] }>(fetcher, 5000)
  const kanalar = data?.kanalar ?? []

  // Lokalt speil av utvalet; null = alt synleg. Bumpar ved lagring.
  const [utval, setUtval] = useState<Set<string> | null>(() => hentSynlegeKanalar())
  const [opneNodar, setOpneNodar] = useState<Set<string>>(new Set())
  // Bumpar når node-rekkefølgja endrar seg, so lista sorterast om straks.
  const [orderVer, setOrderVer] = useState(0)

  const key = (k: HubKanal) => `${k.node_id}:${k.namn}`
  const alle = useMemo(() => new Set(kanalar.map(key)), [kanalar])
  const erSynleg = (nk: string) => (utval === null ? true : utval.has(nk))

  const skriv = (neste: Set<string>) => { setUtval(neste); lagreSynlegeKanalar(neste) }
  const start = () => (utval === null ? new Set(alle) : new Set(utval))

  const toggleKanal = (nk: string) => {
    const s = start(); s.has(nk) ? s.delete(nk) : s.add(nk); skriv(s)
  }

  // Grupper per node, deretter sorter etter brukarvald node-rekkefølgje.
  const nodar = useMemo(() => {
    const m = new Map<string, { namn: string; kanalar: HubKanal[] }>()
    for (const k of kanalar) {
      const g = m.get(k.node_id) ?? { namn: k.node_namn || k.node_id, kanalar: [] }
      g.kanalar.push(k); m.set(k.node_id, g)
    }
    return sorterEtterRekkefolgje([...m.entries()], ([id]) => id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kanalar, orderVer])

  // Flytt ein node opp/ned i dashbord-rekkefølgja.
  const flyttNode = (nodeId: string, retning: -1 | 1) => {
    const ids = nodar.map(([id]) => id)
    const i = ids.indexOf(nodeId)
    const j = i + retning
    if (i < 0 || j < 0 || j >= ids.length) return
    ;[ids[i], ids[j]] = [ids[j], ids[i]]
    lagreNodeRekkefolgje(ids)
    setOrderVer((v) => v + 1)
  }
  const kanFlytte = nodar.length > 1

  const nodeState = (ch: HubKanal[]) => {
    const på = ch.filter((k) => erSynleg(key(k))).length
    return på === 0 ? 'none' : på === ch.length ? 'all' : 'some'
  }
  const toggleNode = (ch: HubKanal[]) => {
    const s = start(); const allePå = ch.every((k) => s.has(key(k)))
    ch.forEach((k) => (allePå ? s.delete(key(k)) : s.add(key(k)))); skriv(s)
  }

  const synlege = kanalar.filter((k) => erSynleg(key(k))).length
  const alleSynlege = kanalar.length > 0 && synlege === kanalar.length

  return (
    <Panel kicker={t('Dashboard')} title={t('Channels shown')}
      sub={t('Choose which nodes and channels appear on the dashboard. Saved on this device.')}
      right={
        <button className="btn-ghost" onClick={() => skriv(alleSynlege ? new Set() : new Set(alle))}>
          {alleSynlege ? t('Hide all') : t('Show all')}
        </button>
      }>
      {kanalar.length === 0 ? (
        <div className="hint">{t('No channels received yet.')}</div>
      ) : (
        <>
          <div className="hint mb-2">{synlege} {t('of')} {kanalar.length} {t('channels shown')}
            {kanFlytte ? ` · ${t('use ▲▼ to reorder nodes on the dashboard')}` : ''}</div>
          <div className="flex flex-col gap-1">
            {nodar.map(([nodeId, g], idx) => {
              const st = nodeState(g.kanalar)
              const open = opneNodar.has(nodeId)
              return (
                <div key={nodeId} style={{ border: '1px solid var(--color-line-soft)' }}>
                  <div className="flex items-center gap-2 px-2 py-1.5">
                    {kanFlytte && (
                      <span className="flex flex-col leading-none">
                        <button title={t('Move up')} disabled={idx === 0}
                          className="text-[10px] text-gray-400 hover:text-[#D76428] disabled:opacity-30 disabled:hover:text-gray-400"
                          onClick={() => flyttNode(nodeId, -1)}>▲</button>
                        <button title={t('Move down')} disabled={idx === nodar.length - 1}
                          className="text-[10px] text-gray-400 hover:text-[#D76428] disabled:opacity-30 disabled:hover:text-gray-400"
                          onClick={() => flyttNode(nodeId, 1)}>▼</button>
                      </span>
                    )}
                    <input type="checkbox" checked={st === 'all'}
                      ref={(el) => { if (el) el.indeterminate = st === 'some' }}
                      onChange={() => toggleNode(g.kanalar)} />
                    <button className="text-[14px] font-semibold text-left flex-1" style={{ fontFamily: 'var(--font-heading)' }}
                      onClick={() => setOpneNodar((p) => { const n = new Set(p); n.has(nodeId) ? n.delete(nodeId) : n.add(nodeId); return n })}>
                      {g.namn} <span className="hint">({g.kanalar.filter((k) => erSynleg(key(k))).length}/{g.kanalar.length})</span>
                    </button>
                    <span className="text-gray-400 text-xs">{open ? '▾' : '▸'}</span>
                  </div>
                  {open && (
                    <div className="px-2 pb-2 grid grid-cols-1 sm:grid-cols-2 gap-x-4">
                      {g.kanalar.map((k) => {
                        const nk = key(k)
                        return (
                          <label key={nk} className="flex items-center gap-2 py-0.5 text-[13px] cursor-pointer">
                            <input type="checkbox" checked={erSynleg(nk)} onChange={() => toggleKanal(nk)} />
                            <span className="truncate flex-1">{k.namn}</span>
                            <span className="hint ui-num">{k.verdi === null ? '—' : k.verdi.toFixed(1)}{k.eining ? ` ${k.eining}` : ''}</span>
                          </label>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </Panel>
  )
}
