import { locale, t } from '../i18n/core'

function fixed2(value: number): string {
  return value.toLocaleString(locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatBytes(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex++
  }
  return `${fixed2(value)} ${units[unitIndex]}`
}

export function formatBps(bps: number): string {
  const units = ['bps', 'Kbps', 'Mbps', 'Gbps']
  let value = bps
  let unitIndex = 0
  while (value >= 1000 && unitIndex < units.length - 1) {
    value /= 1000
    unitIndex++
  }
  return `${fixed2(value)} ${units[unitIndex]}`
}

/** A whole number with the language's thousands separator. */
export function formatCount(value: number): string {
  return Math.round(value).toLocaleString(locale())
}

/** Date and time in the current language. */
export function formatDateTime(iso: string | number): string {
  return new Date(iso).toLocaleString(locale())
}

export function formatDate(iso: string | number): string {
  return new Date(iso).toLocaleDateString(locale())
}

/** "hace X min"-style freshness for an ISO timestamp; "nunca" when null. */
export function formatSince(iso: string | null, now: number = Date.now()): string {
  if (!iso) return t('since.never')
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return t('since.underMinute')
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return t('since.minutes', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return t('since.hours', { n: hours })
  return t('since.days', { n: Math.floor(hours / 24) })
}

/** Axis/tooltip time label: time of day for 24h, day + time for 7d, day for longer. */
export function formatChartTime(time: number, hours: number): string {
  const date = new Date(time)
  if (hours <= 24) return date.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' })
  if (hours <= 24 * 7)
    return date.toLocaleString(locale(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  return date.toLocaleDateString(locale(), { day: '2-digit', month: '2-digit' })
}

export function formatPercent(value: number): string {
  return `${value.toLocaleString(locale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`
}
