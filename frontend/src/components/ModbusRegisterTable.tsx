import type { ModbusRegister, ModbusFunksjon, ModbusDatatype, ModbusByteOrder } from '../api/types'
import { useI18n } from '../i18n'

const FUNKSJONAR: ModbusFunksjon[] = ['holding', 'input', 'coil', 'discrete']
const DATATYPAR: ModbusDatatype[] = ['float32', 'int16', 'uint16', 'int32', 'uint32']
const BYTE_ORDERS: ModbusByteOrder[] = ['AB_CD', 'CD_AB', 'BA_DC', 'DC_BA']

export function tomtRegister(): ModbusRegister {
  return {
    namn: '',
    adresse: 0,
    funksjon: 'holding',
    datatype: 'float32',
    byte_order: 'AB_CD',
    skalering: 1.0,
    offset: 0.0,
    eining: '',
    range_low: -1000,
    range_high: 1000,
  }
}

interface Props {
  registers: ModbusRegister[]
  onChange: (registers: ModbusRegister[]) => void
}

export default function ModbusRegisterTable({ registers, onChange }: Props) {
  const { t } = useI18n()

  const oppdater = (idx: number, patch: Partial<ModbusRegister>) => {
    onChange(registers.map((r, i) => i === idx ? { ...r, ...patch } : r))
  }

  const fjern = (idx: number) => {
    onChange(registers.filter((_, i) => i !== idx))
  }

  const leggTil = () => {
    onChange([...registers, tomtRegister()])
  }

  return (
    <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-semibold text-gray-700">
          {t('Modbus registers')} ({registers.length})
        </h4>
        <button
          type="button"
          onClick={leggTil}
          className="text-xs font-medium px-2.5 py-1 rounded-md bg-[#D76428] text-white hover:bg-[#c55a23]"
        >
          {t('+ Add register')}
        </button>
      </div>

      {registers.length === 0 ? (
        <p className="text-xs text-gray-500 italic py-2 text-center">
          {t('No registers. Add one to get started.')}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-300">
                <th className="py-1 pr-2 font-medium">{t('Name')}</th>
                <th className="py-1 pr-2 font-medium">{t('Address')}</th>
                <th className="py-1 pr-2 font-medium">{t('Function')}</th>
                <th className="py-1 pr-2 font-medium">{t('Data type')}</th>
                <th className="py-1 pr-2 font-medium">{t('Byte order')}</th>
                <th className="py-1 pr-2 font-medium">{t('Scaling')}</th>
                <th className="py-1 pr-2 font-medium">{t('Offset')}</th>
                <th className="py-1 pr-2 font-medium">{t('Unit')}</th>
                <th className="py-1 pr-2 font-medium">{t('Range min')}</th>
                <th className="py-1 pr-2 font-medium">{t('Range max')}</th>
                <th className="py-1 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {registers.map((r, idx) => (
                <tr key={idx} className="border-b border-gray-200 last:border-0">
                  <td className="py-1 pr-2">
                    <input
                      type="text" value={r.namn}
                      onChange={e => oppdater(idx, { namn: e.target.value })}
                      placeholder="V_L1_N"
                      className="w-24 border border-gray-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    />
                  </td>
                  <td className="py-1 pr-2">
                    <input
                      type="number" value={r.adresse}
                      onChange={e => oppdater(idx, { adresse: parseInt(e.target.value) || 0 })}
                      className="w-20 border border-gray-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    />
                  </td>
                  <td className="py-1 pr-2">
                    <select
                      value={r.funksjon}
                      onChange={e => oppdater(idx, { funksjon: e.target.value as ModbusFunksjon })}
                      className="border border-gray-300 rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    >
                      {FUNKSJONAR.map(f => <option key={f} value={f}>{f}</option>)}
                    </select>
                  </td>
                  <td className="py-1 pr-2">
                    <select
                      value={r.datatype}
                      onChange={e => oppdater(idx, { datatype: e.target.value as ModbusDatatype })}
                      className="border border-gray-300 rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    >
                      {DATATYPAR.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </td>
                  <td className="py-1 pr-2">
                    <select
                      value={r.byte_order}
                      onChange={e => oppdater(idx, { byte_order: e.target.value as ModbusByteOrder })}
                      disabled={r.datatype === 'int16' || r.datatype === 'uint16' || r.funksjon === 'coil' || r.funksjon === 'discrete'}
                      className="border border-gray-300 rounded px-1 py-0.5 disabled:bg-gray-100 disabled:text-gray-400 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    >
                      {BYTE_ORDERS.map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                  </td>
                  <td className="py-1 pr-2">
                    <input
                      type="number" step="any" value={r.skalering}
                      onChange={e => oppdater(idx, { skalering: parseFloat(e.target.value) || 0 })}
                      className="w-16 border border-gray-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    />
                  </td>
                  <td className="py-1 pr-2">
                    <input
                      type="number" step="any" value={r.offset}
                      onChange={e => oppdater(idx, { offset: parseFloat(e.target.value) || 0 })}
                      className="w-16 border border-gray-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    />
                  </td>
                  <td className="py-1 pr-2">
                    <input
                      type="text" value={r.eining}
                      onChange={e => oppdater(idx, { eining: e.target.value })}
                      placeholder="V"
                      className="w-14 border border-gray-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    />
                  </td>
                  <td className="py-1 pr-2">
                    <input
                      type="number" step="any" value={r.range_low}
                      onChange={e => oppdater(idx, { range_low: parseFloat(e.target.value) || 0 })}
                      className="w-20 border border-gray-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    />
                  </td>
                  <td className="py-1 pr-2">
                    <input
                      type="number" step="any" value={r.range_high}
                      onChange={e => oppdater(idx, { range_high: parseFloat(e.target.value) || 0 })}
                      className="w-20 border border-gray-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#D76428]"
                    />
                  </td>
                  <td className="py-1 text-right">
                    <button
                      type="button"
                      onClick={() => fjern(idx)}
                      className="text-red-600 hover:text-red-800 px-1"
                      title={t('Remove')}
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
