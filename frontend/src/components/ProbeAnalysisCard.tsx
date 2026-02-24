import { useState } from 'react'
import {
  kjorProbe,
  kjorProtokoll,
  kjorDekoder,
  kjorAdc,
  kjorSniffer,
} from '../api/probe'
import { useProbeRunner } from '../hooks/useProbeRunner'
import OutputTerminal from './OutputTerminal'
import ReportList from './ReportList'

export default function ProbeAnalysisCard() {
  const { status, output, startPolling } = useProbeRunner()
  const [statusMsg, setStatusMsg] = useState('')
  const running = status === 'running'

  const run = async (label: string, fn: () => Promise<{ suksess: boolean; melding: string }>) => {
    setStatusMsg(`Starter ${label}...`)
    try {
      const res = await fn()
      if (!res.suksess) {
        setStatusMsg(res.melding)
        return
      }
      startPolling()
    } catch (e) {
      setStatusMsg('Nettverksfeil: ' + String(e))
    }
  }

  return (
    <div className="kort">
      <h2>SIRIUS USB-analyse</h2>

      <div style={{
        padding: '0.5rem 0.75rem',
        background: '#FDF2EC',
        border: '1px solid #E8753A',
        borderRadius: '0.5rem',
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        marginBottom: '0.75rem',
      }}>
        <button
          className="btn"
          disabled={running}
          onClick={() => run('sniffer', () => kjorSniffer(15))}
          style={{ background: '#D76428', color: '#fff', fontWeight: 600, whiteSpace: 'nowrap' }}
        >
          Fang DewesoftX-trafikk (15s)
        </button>
        <span style={{ color: '#B85420', fontSize: '0.8rem' }}>
          Passiv fangst via usbmon — forstyrrar IKKJE USB/IP eller DewesoftX.
        </span>
      </div>

      <details style={{ marginBottom: '0.75rem' }}>
        <summary style={{ cursor: 'pointer', color: '#6b6b6b', fontSize: '0.85rem' }}>
          Direkte USB-tester (stopper USB/IP-deling!)
        </summary>
        <p style={{ color: '#991b1b', fontSize: '0.8rem', margin: '0.5rem 0' }}>
          Desse verktøya tek kontroll over SIRIUS USB og avbryt USB/IP.
          DewesoftX mistar tilkoplinga. Bruk berre når USB/IP er stoppa.
        </p>
        <div className="usbip-knapper">
          <button className="btn btn-blaa" disabled={running} onClick={() => run('probe', kjorProbe)}>
            USB Deskriptorer
          </button>
          <button className="btn btn-blaa" disabled={running} onClick={() => run('protokoll', () => kjorProtokoll('scan'))}>
            Skann kommandoer
          </button>
          <button className="btn btn-blaa" disabled={running} onClick={() => run('dekoder', () => kjorDekoder('info'))}>
            Dekod enhetsinfo
          </button>
          <button
            className="btn"
            disabled={running}
            onClick={() => run('ADC', () => kjorAdc(5))}
            style={{ background: '#E8753A', color: '#fff', fontWeight: 600 }}
          >
            Les ADC (5s)
          </button>
          <button
            className="btn"
            disabled={running}
            onClick={() => run('ADC+lagre', () => kjorAdc(10, true))}
            style={{ background: '#D76428', color: '#fff', fontWeight: 600 }}
          >
            Les + Lagre (10s)
          </button>
        </div>
      </details>

      {statusMsg && (
        <div className={`melding ${status === 'error' ? 'melding-feil' : 'melding-ok'}`}>
          {status === 'done' ? 'Fullført' : status === 'error' ? 'Feil' : statusMsg}
        </div>
      )}

      <OutputTerminal text={running ? `Køyrer...\n${output}` : output} />

      <ReportList />
    </div>
  )
}
