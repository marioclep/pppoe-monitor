import type { MessageKey } from '../i18n/core'

export type RouterStatus = 'ok' | 'late' | 'down'

/** Translation key of each status (useT().t(ROUTER_STATUS_KEY[status])). */
export const ROUTER_STATUS_KEY: Record<RouterStatus, MessageKey> = {
  ok: 'status.ok',
  late: 'status.late',
  down: 'status.down',
}

/** ok < 2 polling intervals since the last successful poll, late < 6, else down. */
export function routerStatus(lastPolledAt: string | null, intervalSeconds: number, now: number = Date.now()): RouterStatus {
  if (!lastPolledAt) return 'down'
  const ageSeconds = (now - new Date(lastPolledAt).getTime()) / 1000
  if (ageSeconds < 2 * intervalSeconds) return 'ok'
  if (ageSeconds < 6 * intervalSeconds) return 'late'
  return 'down'
}
