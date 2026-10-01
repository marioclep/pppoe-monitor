import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { apiFetch } from '../api/client'
import { useT } from '../i18n'
import type { Lang } from '../i18n'

interface SiteContextValue {
  /** The installation's name from Configuración ("ACME"), or null. */
  siteName: string | null
  setSiteName: (name: string | null) => void
}

const SiteContext = createContext<SiteContextValue | undefined>(undefined)

/** Mounted only inside the logged-in layout: /settings needs a session.
 *  Also applies the installation's language. */
export function SiteProvider({ children }: { children: ReactNode }) {
  const [siteName, setSiteName] = useState<string | null>(null)
  const { t, setLang } = useT()

  useEffect(() => {
    apiFetch<{ site_name: string | null; language: Lang }>('/settings')
      .then((s) => {
        setSiteName(s.site_name)
        setLang(s.language)
      })
      .catch(() => {
        /* no name: plain titles */
      })
  }, [setLang])

  // The browser tab tells installations apart; back to plain on logout.
  const appTitle = t('app.name')
  useEffect(() => {
    document.title = siteName ? `${appTitle} · ${siteName}` : appTitle
    return () => {
      document.title = appTitle
    }
  }, [siteName, appTitle])

  return <SiteContext.Provider value={{ siteName, setSiteName }}>{children}</SiteContext.Provider>
}

export function useSite(): SiteContextValue {
  const ctx = useContext(SiteContext)
  if (!ctx) throw new Error('useSite must be used within SiteProvider')
  return ctx
}
