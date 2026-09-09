import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import {
  startMqttOppdag, fetchMqttOppdag, stoppMqttOppdag, leggTilMqttKanal,
} from '../api/mqtt'
import type { MqttOppdagStatus, MqttForslag } from '../api/mqtt'
import Panel from './ui/Panel'
import { useI18n } from '../i18n'

/**
 * MQTT topic-oppdaging — "avlytt" brokeren for å finne topics, og få dei
 * som ser ut som straumdata føreslått som kanalar.
 *
 * Abonnerer på # ei kort stund (backend), listar topics med interessante
 * (spenning/straum/effekt/energi/frekvens) først, og lèt deg leggje eit
 * topic/JSON-felt til som ein MQTT-kanal — som deretter strøymer over
 * openDAQ som resten.
 */
export default function MqttOppdagCard() {
  const { t } = useI18n()
  const [varigheit, setVarigheit] = useState(15)
  const [feil, setFeil] = useState<string | null>(null)
  const [lagt, setLagt] = useState<Record<string, string>>({})

  const fetcher = useCallback(() => fetchMqttOppdag(), [])
  const { data, refresh } = usePolling<MqttOppdagStatus>(fetcher, 1500)
  const koeyrer = data?.tilstand === 'koeyrer'

  const start = async () => {
    setFeil(null); setLagt({})
    try {
      const r = await startMqttOppdag(varigheit)
      if (!r.suksess) setFeil(r.melding)
      refresh()
    } catch (e) { setFeil(e instanceof Error ? e.message : String(e)) }
  }
  const stopp = async () => { try { await stoppMqttOppdag(); refresh() } catch { /* status viser */ } }

  const leggTil = async (topic: string, f: MqttForslag) => {
    const nokkel = `${topic}|${f.json_sti}`
    setLagt((m) => ({ ...m, [nokkel]: '…' }))
    try {
      const r = await leggTilMqttKanal({
        topic, json_sti: f.json_sti, namn: f.namn, eining: f.eining,
      })
      setLagt((m) => ({ ...m, [nokkel]: r.suksess ? t('added') : (r.melding || t('failed')) }))
    } catch (e) {
      setLagt((m) => ({ ...m, [nokkel]: e instanceof Error ? e.message : String(e) }))
    }
  }

  return (
    <Panel kicker={t('Discovery')} title={t('MQTT topic discovery')}
      sub={t('Listen to the broker to find topics. Ones that look like power data are suggested as channels — add one and it streams over openDAQ like the rest.')}>

      {feil && <div className="mb-3 p-2 text-sm" style={{ background: 'var(--color-accent-100)', color: '#b45309' }}>{feil}</div>}

      <div className="flex items-end gap-2 mb-3 flex-wrap">
        <div>
          <label className="ui-label">{t('Listen for (seconds)')}</label>
          <input className="ui-input" style={{ width: 90 }} type="number" min={3} max={120}
            value={varigheit} onChange={(e) => setVarigheit(Number(e.target.value))} disabled={koeyrer} />
        </div>
        {koeyrer ? (
          <button className="btn-ghost" onClick={stopp}>{t('Stop')}</button>
        ) : (
          <button className="btn-primary" onClick={start}>{t('Listen')}</button>
        )}
        {data && (data.tilstand || '') !== '' && (
          <span className="hint ml-auto">
            {koeyrer ? `${t('Listening…')} ${data.sekund_att}s` : data.melding}
          </span>
        )}
      </div>

      {koeyrer && (
        <div className="h-1 mb-3" style={{ background: 'var(--color-line-soft)' }}>
          <div className="h-full" style={{ background: 'var(--color-accent)', width: `${100 * (1 - (data?.sekund_att ?? 0) / Math.max(1, varigheit))}%`, transition: 'width .5s linear' }} />
        </div>
      )}

      {(data?.topics?.length ?? 0) > 0 && (
        <div className="flex flex-col gap-2">
          {data!.topics.map((tp) => (
            <div key={tp.topic} className="meta-panel"
              style={tp.interessant ? { borderColor: 'var(--color-accent)' } : undefined}>
              <div className="flex items-center gap-2">
                <span className="ui-num text-[13px] font-semibold" style={{ fontFamily: 'var(--font-mono)' }}>{tp.topic}</span>
                {tp.interessant && <span className="tag tag-accent">{t('power data')}</span>}
                <span className="hint ml-auto">{tp.tal} {t('msgs')}</span>
              </div>
              <div className="hint mt-0.5 truncate" title={tp.sist_payload}>{tp.sist_payload}</div>
              <div className="mt-1.5 flex flex-col gap-1">
                {tp.forslag.map((f) => {
                  const nokkel = `${tp.topic}|${f.json_sti}`
                  const status = lagt[nokkel]
                  return (
                    <div key={nokkel} className="flex items-center gap-2 text-[13px] py-0.5"
                      style={{ borderTop: '1px solid var(--color-line-soft)' }}>
                      <span className="ui-num" style={{ minWidth: 90 }}>{f.json_sti || t('(whole payload)')}</span>
                      <span className="ui-num" style={{ color: f.interessant ? 'var(--color-accent-800)' : undefined }}>{f.verdi}</span>
                      {f.kvantitet && <span className="hint">{t(f.kvantitet)}{f.eining ? ` · ${f.eining}` : ''}</span>}
                      {status ? (
                        <span className="ml-auto hint" style={{ color: status === t('added') ? 'var(--color-accent-700)' : undefined }}>{status}</span>
                      ) : (
                        <button className="btn-ghost ml-auto" onClick={() => leggTil(tp.topic, f)}>{t('Add as channel')}</button>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {data && !koeyrer && data.tilstand === 'ferdig' && (data.topics?.length ?? 0) === 0 && (
        <div className="hint">{t('No topics seen. Is the broker publishing, and is the wildcard allowed?')}</div>
      )}
    </Panel>
  )
}
