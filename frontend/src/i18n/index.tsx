import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { LOCALES, apply, currentLang, translate } from './core'
import type { Lang, MessageKey, Params } from './core'

export type { Lang, MessageKey, Params } from './core'

interface LanguageContextValue {
  lang: Lang
  setLang: (lang: Lang) => void
  t: (key: MessageKey, params?: Params) => string
  locale: string
}

const LanguageContext = createContext<LanguageContextValue | undefined>(undefined)

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(currentLang)
  // Before the children render, so formatters already use the new language.
  if (currentLang() !== lang || document.documentElement.lang !== lang) apply(lang)

  const setLang = useCallback((next: Lang) => {
    apply(next)
    setLangState(next)
  }, [])

  const value = useMemo(
    () => ({ lang, setLang, t: (key: MessageKey, params?: Params) => translate(lang, key, params), locale: LOCALES[lang] }),
    [lang, setLang],
  )
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
}

export function useT(): LanguageContextValue {
  const ctx = useContext(LanguageContext)
  if (!ctx) throw new Error('useT must be used within LanguageProvider')
  return ctx
}
