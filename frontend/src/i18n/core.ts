import { en } from './en'
import { es } from './es'

export type Lang = 'es' | 'en'
export type MessageKey = keyof typeof es
export type Params = Record<string, string | number>

// en is typed against es: a key missing in either one fails the build.
const DICTS: Record<Lang, Record<MessageKey, string>> = { es, en }
export const LOCALES: Record<Lang, string> = { es: 'es-AR', en: 'en-US' }
const STORAGE_KEY = 'pppoe_lang'

/** Before login the installation's language is unknown: the last one seen in
 *  this browser, else the browser's own. */
function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'es' || saved === 'en') return saved
  } catch {
    /* storage blocked */
  }
  return navigator.language?.toLowerCase().startsWith('en') ? 'en' : 'es'
}

let current: Lang = initialLang()

export function currentLang(): Lang {
  return current
}

export function translate(lang: Lang, key: MessageKey, params?: Params): string {
  let text = DICTS[lang][key]
  if (params) for (const [name, value] of Object.entries(params)) text = text.replaceAll(`{${name}}`, String(value))
  return text
}

export function apply(lang: Lang) {
  current = lang
  document.documentElement.lang = lang
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    /* storage blocked */
  }
}

/** For code outside components (formatters). Components use useT(), which
 *  also re-renders them when the language changes. */
export function t(key: MessageKey, params?: Params): string {
  return translate(current, key, params)
}

/** The Intl locale of the current language ("es-AR" / "en-US"). */
export function locale(): string {
  return LOCALES[current]
}
