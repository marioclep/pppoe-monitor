import { locale } from '../i18n/core'
import type { MessageKey } from '../i18n/core'

// On a PPPoE-server interface TX is the client's download and RX its upload;
// the API already speaks in download/upload.
export type Direction = 'download' | 'upload'

export const DIRECTIONS: Direction[] = ['download', 'upload']

export const DIRECTION_KEY: Record<Direction, MessageKey> = { download: 'common.download', upload: 'common.upload' }

export const CHANNEL_LABEL: Record<string, string> = { email: 'Email', telegram: 'Telegram' }

/** Same unit as formatBytes and the alert messages. */
export const GB = 1024 ** 3

export interface Threshold {
  id: number
  client_id: number | null
  client_username: string | null
  router_name: string | null
  direction: Direction
  bytes_threshold: number
  notify_channel: string
}

/** "500 GB" for whole numbers, "523,40 GB" otherwise. */
export function formatGb(bytes: number): string {
  const digits = bytes % GB === 0 ? 0 : 2
  return `${(bytes / GB).toLocaleString(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits })} GB`
}
