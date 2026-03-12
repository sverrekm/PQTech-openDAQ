import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

export type Lang = 'en' | 'nb'

interface I18nContextValue {
  lang: Lang
  setLang: (lang: Lang) => void
  t: (english: string) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

const STORAGE_KEY = 'pqtech_lang'

function getInitialLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'nb') return 'nb'
  } catch { /* ignore */ }
  return 'en'
}

import { nb } from './nb'

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(getInitialLang)

  const setLang = useCallback((newLang: Lang) => {
    setLangState(newLang)
    try {
      localStorage.setItem(STORAGE_KEY, newLang)
    } catch { /* ignore */ }
  }, [])

  const t = useCallback((english: string): string => {
    if (lang === 'en') return english
    return nb[english] ?? english
  }, [lang])

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useI18n() {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useI18n must be used within I18nProvider')
  return ctx
}
