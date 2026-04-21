import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

export type ToastVariant = 'success' | 'error' | 'info' | 'warning'

export interface Toast {
  id: number
  melding: string
  variant: ToastVariant
  varigheit_ms: number
}

interface ToastContextValue {
  vis: (melding: string, variant?: ToastVariant, varigheit_ms?: number) => void
  suksess: (melding: string, varigheit_ms?: number) => void
  feil: (melding: string, varigheit_ms?: number) => void
  info: (melding: string, varigheit_ms?: number) => void
  aatvar: (melding: string, varigheit_ms?: number) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let toastIdTeller = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const fjern = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const vis = useCallback((
    melding: string,
    variant: ToastVariant = 'info',
    varigheit_ms: number = 4000,
  ) => {
    const id = ++toastIdTeller
    setToasts(prev => [...prev, { id, melding, variant, varigheit_ms }])
  }, [])

  const suksess = useCallback((m: string, v?: number) => vis(m, 'success', v), [vis])
  const feil = useCallback((m: string, v?: number) => vis(m, 'error', v ?? 6000), [vis])
  const info = useCallback((m: string, v?: number) => vis(m, 'info', v), [vis])
  const aatvar = useCallback((m: string, v?: number) => vis(m, 'warning', v ?? 5000), [vis])

  return (
    <ToastContext.Provider value={{ vis, suksess, feil, info, aatvar }}>
      {children}
      <ToastContainer toasts={toasts} onFjern={fjern} />
    </ToastContext.Provider>
  )
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast må brukast inne i ToastProvider')
  return ctx
}

function ToastContainer({ toasts, onFjern }: { toasts: Toast[], onFjern: (id: number) => void }) {
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none max-w-sm w-full">
      {toasts.map(t => (
        <ToastItem key={t.id} toast={t} onFjern={onFjern} />
      ))}
    </div>
  )
}

function ToastItem({ toast, onFjern }: { toast: Toast, onFjern: (id: number) => void }) {
  const [synleg, setSynleg] = useState(false)

  useEffect(() => {
    // Animer inn
    const t1 = setTimeout(() => setSynleg(true), 10)
    // Auto-dismiss
    const t2 = setTimeout(() => {
      setSynleg(false)
      setTimeout(() => onFjern(toast.id), 200) // Vent på utgangsanimasjon
    }, toast.varigheit_ms)
    return () => {
      clearTimeout(t1)
      clearTimeout(t2)
    }
  }, [toast.id, toast.varigheit_ms, onFjern])

  const handleLukk = () => {
    setSynleg(false)
    setTimeout(() => onFjern(toast.id), 200)
  }

  const baseClass = 'pointer-events-auto rounded-lg shadow-lg border px-4 py-3 text-sm flex items-start gap-3 transition-all duration-200 cursor-pointer'
  const varianter: Record<ToastVariant, string> = {
    success: 'bg-green-50 border-green-300 text-green-900',
    error: 'bg-red-50 border-red-300 text-red-900',
    warning: 'bg-amber-50 border-amber-300 text-amber-900',
    info: 'bg-blue-50 border-blue-300 text-blue-900',
  }
  const ikon: Record<ToastVariant, string> = {
    success: '✓',
    error: '✕',
    warning: '⚠',
    info: 'ⓘ',
  }
  const animasjon = synleg
    ? 'translate-x-0 opacity-100'
    : 'translate-x-4 opacity-0'

  return (
    <div
      className={`${baseClass} ${varianter[toast.variant]} ${animasjon}`}
      onClick={handleLukk}
      role="alert"
    >
      <span className="font-bold flex-shrink-0" aria-hidden="true">{ikon[toast.variant]}</span>
      <span className="flex-1 break-words">{toast.melding}</span>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); handleLukk() }}
        className="text-current opacity-50 hover:opacity-100 flex-shrink-0 -mr-1"
        aria-label="Lukk"
      >
        ×
      </button>
    </div>
  )
}
