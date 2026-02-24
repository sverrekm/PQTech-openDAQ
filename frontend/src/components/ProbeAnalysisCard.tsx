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
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">SIRIUS USB-analyse</h2>

      <div className="p-3 bg-orange-50 border border-orange-300 rounded-lg flex items-center gap-3 mb-3">
        <button
          className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out whitespace-nowrap"
          disabled={running}
          onClick={() => run('sniffer', () => kjorSniffer(15))}
        >
          Fang DewesoftX-trafikk (15s)
        </button>
        <span className="text-orange-700 text-xs">
          Passiv fangst via usbmon — forstyrrar IKKJE USB/IP eller DewesoftX.
        </span>
      </div>

      <details className="mb-3">
        <summary className="cursor-pointer text-gray-500 text-sm">
          Direkte USB-tester (stopper USB/IP-deling!)
        </summary>
        <p className="text-red-700 text-xs my-2">
          Desse verktøya tek kontroll over SIRIUS USB og avbryt USB/IP.
          DewesoftX mistar tilkoplinga. Bruk berre når USB/IP er stoppa.
        </p>
        <div className="flex flex-wrap gap-2">
          <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={running} onClick={() => run('probe', kjorProbe)}>
            USB Deskriptorer
          </button>
          <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={running} onClick={() => run('protokoll', () => kjorProtokoll('scan'))}>
            Skann kommandoer
          </button>
          <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={running} onClick={() => run('dekoder', () => kjorDekoder('info'))}>
            Dekod enhetsinfo
          </button>
          <button
            className="bg-orange-500 hover:bg-orange-600 text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={running}
            onClick={() => run('ADC', () => kjorAdc(5))}
          >
            Les ADC (5s)
          </button>
          <button
            className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={running}
            onClick={() => run('ADC+lagre', () => kjorAdc(10, true))}
          >
            Les + Lagre (10s)
          </button>
        </div>
      </details>

      {statusMsg && (
        <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${status === 'error' ? 'bg-red-100 text-red-800' : 'bg-green-100 text-green-800'}`}>
          {status === 'done' ? 'Fullført' : status === 'error' ? 'Feil' : statusMsg}
        </div>
      )}

      <OutputTerminal text={running ? `Køyrer...\n${output}` : output} />

      <ReportList />
    </div>
  )
}
