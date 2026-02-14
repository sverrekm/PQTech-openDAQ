import { useState, useCallback } from 'react'
import { fetchUsbIpStatus, usbipDel, usbipStopp } from '../api/usbip'
import { usePolling } from '../hooks/usePolling'
import InfoGrid from './InfoGrid'
import CopyableCommand from './CopyableCommand'

interface Props {
  ip: string
}

export default function UsbIpCard({ ip }: Props) {
  const fetcher = useCallback(() => fetchUsbIpStatus(), [])
  const { data: u, refresh } = usePolling(fetcher, 5000)
  const [feil, setFeil] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (!u) return null

  const handleDel = async () => {
    setBusy(true)
    setFeil(null)
    try {
      const res = await usbipDel()
      if (!res.suksess) setFeil(res.melding)
      refresh()
    } catch (e) {
      setFeil('Nettverksfeil: ' + String(e))
    }
    setBusy(false)
  }

  const handleStopp = async () => {
    setBusy(true)
    setFeil(null)
    try {
      await usbipStopp()
      refresh()
    } catch (e) {
      setFeil('Nettverksfeil: ' + String(e))
    }
    setBusy(false)
  }

  return (
    <div className="kort">
      <h2>USB/IP — DEL SIRIUS</h2>
      <div className="usbip-status-rad">
        <span className={`dot ${u.deling_aktiv ? 'dot-gronn' : 'dot-rod'}`} />
        <span>
          {u.deling_aktiv
            ? 'Deling aktiv paa port 3240'
            : u.tilgjengelig ? 'Klar' : 'USB/IP utilgjengelig'}
        </span>
      </div>
      <InfoGrid items={[
        {
          label: 'SIRIUS paa USB',
          value: u.sirius_paa_usb ? (u.sirius_enhet_funnet || 'Ja') : 'Nei',
          color: u.sirius_paa_usb ? '#10b981' : '#ef4444',
        },
        { label: 'Bus-ID', value: u.busid || u.sirius_busid_funnet || '-' },
        {
          label: 'Deling',
          value: u.deling_aktiv ? 'Aktiv' : 'Inaktiv',
          color: u.deling_aktiv ? '#10b981' : '#6b6b6b',
        },
      ]} />
      {(feil || u.feil) && (
        <div className="melding melding-feil">{feil || u.feil}</div>
      )}
      <div className="usbip-knapper">
        {!u.deling_aktiv && (
          <button
            className="btn btn-gronn"
            disabled={busy || !u.sirius_paa_usb || !u.tilgjengelig}
            onClick={handleDel}
          >
            Del SIRIUS via USB/IP
          </button>
        )}
        {u.deling_aktiv && (
          <button className="btn btn-rod" disabled={busy} onClick={handleStopp}>
            Stopp deling
          </button>
        )}
      </div>
      {u.deling_aktiv && (
        <div className="usbip-instruksjoner">
          <h3>Paa Windows-PC</h3>
          <ol>
            <li>
              Installer{' '}
              <a href="https://github.com/cezanne/usbip-win2/releases" target="_blank" rel="noreferrer" style={{ color: '#D76428' }}>
                usbip-win2
              </a>
            </li>
            <li>Apne PowerShell som Administrator</li>
            <li>
              List enheter:
              <CopyableCommand text={`usbip list -r ${ip}`} style={{ marginTop: '0.35rem' }} />
            </li>
            <li>
              Koble til SIRIUS:
              <CopyableCommand
                text={`usbip attach -r ${ip} -b ${u.busid || 'X-Y'}`}
                style={{ marginTop: '0.35rem' }}
              />
            </li>
            <li>Apne DewesoftX — SIRIUS vises som lokal USB-enhet</li>
          </ol>
        </div>
      )}
    </div>
  )
}
