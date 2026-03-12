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
import { useI18n } from '../i18n'

export default function ProbeAnalysisCard() {
  const { t } = useI18n()
  const { status, output, startPolling } = useProbeRunner()
  const [statusMsg, setStatusMsg] = useState('')
  const running = status === 'running'

  const run = async (label: string, fn: () => Promise<{ suksess: boolean; melding: string }>) => {
    setStatusMsg(`${t('Starting...')} ${label}`)
    try {
      const res = await fn()
      if (!res.suksess) {
        setStatusMsg(res.melding)
        return
      }
      startPolling()
    } catch (e) {
      setStatusMsg(t('Network error: ') + String(e))
    }
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-4 shadow-sm">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-4">{t('SIRIUS USB analysis')}</h2>

      <div className="p-3 bg-orange-50 border border-orange-300 rounded-lg flex items-center gap-3 mb-3">
        <button
          className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out whitespace-nowrap"
          disabled={running}
          onClick={() => run('sniffer', () => kjorSniffer(15))}
        >
          {t('Capture DewesoftX traffic (15s)')}
        </button>
        <span className="text-orange-700 text-xs">
          {t('Passive capture via usbmon — does NOT interfere with USB/IP or DewesoftX.')}
        </span>
      </div>

      <details className="mb-3">
        <summary className="cursor-pointer text-gray-500 text-sm">
          {t('Direct USB tests (stops USB/IP sharing!)')}
        </summary>
        <p className="text-red-700 text-xs my-2">
          {t('These tools take control of SIRIUS USB and interrupt USB/IP. DewesoftX loses connection. Only use when USB/IP is stopped.')}
        </p>
        <div className="flex flex-wrap gap-2">
          <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={running} onClick={() => run('probe', kjorProbe)}>
            {t('USB Descriptors')}
          </button>
          <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={running} onClick={() => run('protocol', () => kjorProtokoll('scan'))}>
            {t('Scan commands')}
          </button>
          <button className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed" disabled={running} onClick={() => run('decoder', () => kjorDekoder('info'))}>
            {t('Decode device info')}
          </button>
          <button
            className="bg-orange-500 hover:bg-orange-600 text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={running}
            onClick={() => run('ADC', () => kjorAdc(5))}
          >
            {t('Read ADC (5s)')}
          </button>
          <button
            className="bg-[#D76428] hover:bg-[#B85420] text-white font-medium py-2 px-4 rounded-lg text-sm transition-colors duration-150 ease-in-out disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={running}
            onClick={() => run('ADC+save', () => kjorAdc(10, true))}
          >
            {t('Read + Save (10s)')}
          </button>
        </div>
      </details>

      {statusMsg && (
        <div className={`mt-3 px-3 py-2 rounded-lg text-sm ${status === 'error' ? 'bg-red-100 text-red-800' : 'bg-green-100 text-green-800'}`}>
          {status === 'done' ? t('Completed') : status === 'error' ? t('Error') : statusMsg}
        </div>
      )}

      <OutputTerminal text={running ? `${t('Running...')}\n${output}` : output} />

      <ReportList />
    </div>
  )
}
