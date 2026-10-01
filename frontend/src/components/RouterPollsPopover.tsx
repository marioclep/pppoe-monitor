import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useT } from '../i18n'
import { formatBps, formatCount } from '../utils/format'
import { withGaps } from '../utils/gaps'
import { clientsSeries, trafficSeries } from '../utils/series'
import { ExpandButton } from './ChartPanel'
import { Icon } from './Icon'
import { ChartLegend, TrafficChart } from './TrafficChart'
import type { ChartRow } from './TrafficChart'

interface RouterPoll {
  t: string
  clients_connected: number | null
  rx_bps: number
  tx_bps: number
}

interface RouterPolls {
  router_id: number
  points: RouterPoll[]
}

const HOURS = 24
const CACHE_MS = 60_000
const WIDTH = 420

// Shared by the popover and the expanded view, so hovering the same router
// again within a minute -- or expanding it -- doesn't refetch.
const cache = new Map<number, { at: number; data: RouterPolls }>()

function cachedPolls(routerId: number): RouterPolls | null {
  const hit = cache.get(routerId)
  return hit && Date.now() - hit.at < CACHE_MS ? hit.data : null
}

function useRouterPolls(routerId: number): { data: RouterPolls | null; error: boolean } {
  const [data, setData] = useState<RouterPolls | null>(() => cachedPolls(routerId))
  const [error, setError] = useState(false)

  useEffect(() => {
    if (cachedPolls(routerId)) return
    let cancelled = false
    apiFetch<RouterPolls>(`/dashboard/routers/${routerId}/polls?hours=${HOURS}`)
      .then((d) => {
        cache.set(routerId, { at: Date.now(), data: d })
        if (!cancelled) setData(d)
      })
      .catch(() => !cancelled && setError(true))
    return () => {
      cancelled = true
    }
  }, [routerId])

  return { data, error }
}

export function RouterPollsTitle({ routerName }: { routerName: string }) {
  const { t } = useT()
  return (
    <>
      {routerName} <span className="muted">· {t('polls.subtitle')}</span>
    </>
  )
}

/** Connected clients and traffic of one router, one point per poll. In the
 *  expanded view each chart takes half of the modal. */
export function RouterPollsCharts({
  routerId,
  pollingIntervalSeconds,
  expanded = false,
}: {
  routerId: number
  pollingIntervalSeconds: number
  expanded?: boolean
}) {
  const { data, error } = useRouterPolls(routerId)
  const { t } = useT()
  if (error) return <p className="error">{t('polls.loadError')}</p>
  if (!data) return <p className="empty">{t('common.loading')}</p>
  const TRAFFIC_SERIES = trafficSeries(t)
  const CLIENTS_SERIES = clientsSeries(t)

  const maxGap = 2.5 * pollingIntervalSeconds
  const points: ChartRow[] = data.points.map((p) => ({
    t: Date.parse(p.t),
    download: p.tx_bps,
    upload: p.rx_bps,
    clients: p.clients_connected,
  }))
  const trafficRows = withGaps(points, maxGap, (t) => ({ t, download: null, upload: null, clients: null }))
  const clientRows = withGaps(
    points.filter((p) => p.clients !== null),
    maxGap,
    (t) => ({ t, clients: null }),
  )

  return (
    <>
      <div className={expanded ? 'chart-modal-part' : undefined}>
        <div className="stat-label">{t('chart.connectedClients')}</div>
        <TrafficChart
          data={clientRows}
          series={CLIENTS_SERIES}
          format={formatCount}
          hours={HOURS}
          height={expanded ? '100%' : 110}
        />
      </div>
      <div className={expanded ? 'chart-modal-part' : undefined}>
        <div className="poll-popover-head">
          <span className="stat-label">{t('chart.traffic')}</span>
          <ChartLegend series={TRAFFIC_SERIES} />
        </div>
        <TrafficChart
          data={trafficRows}
          series={TRAFFIC_SERIES}
          format={formatBps}
          hours={HOURS}
          height={expanded ? '100%' : 130}
        />
      </div>
    </>
  )
}

interface Props {
  routerId: number
  routerName: string
  /** Screen rect of the row (or button) the popover points at. */
  anchor: DOMRect
  pollingIntervalSeconds: number
  /** Opened by a tap: shows a close button and closes on Esc or scroll. */
  pinned: boolean
  onClose: () => void
  onExpand: () => void
  /** Hover-opened: the pointer moving onto the popover keeps it open. */
  onMouseEnter?: () => void
  onMouseLeave?: () => void
}

export function RouterPollsPopover({
  routerId,
  routerName,
  anchor,
  pollingIntervalSeconds,
  pinned,
  onClose,
  onExpand,
  onMouseEnter,
  onMouseLeave,
}: Props) {
  const { t } = useT()
  useEffect(() => {
    if (!pinned) return
    const close = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', close)
    window.addEventListener('scroll', onClose, { passive: true })
    return () => {
      window.removeEventListener('keydown', close)
      window.removeEventListener('scroll', onClose)
    }
  }, [pinned, onClose])

  const width = Math.min(WIDTH, window.innerWidth - 32)
  const left = Math.max(16, Math.min(anchor.left, window.innerWidth - width - 16))
  // Below the row, or above it when there is no room below. The 4px overlap
  // lets the pointer cross from the row onto the popover without a gap.
  const below = window.innerHeight - anchor.bottom > 380
  const style = below
    ? { left, width, top: anchor.bottom - 4 }
    : { left, width, bottom: window.innerHeight - anchor.top - 4 }

  return (
    <div
      className="poll-popover"
      style={style}
      role="dialog"
      aria-label={t('polls.ariaLabel', { name: routerName })}
      onClick={(e) => e.stopPropagation()}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <div className="panel-head">
        <h2 className="panel-title"><RouterPollsTitle routerName={routerName} /></h2>
        <div className="panel-actions">
          <ExpandButton onClick={onExpand} />
          {pinned && (
            <button type="button" className="icon-button" aria-label={t('common.close')} onClick={onClose}>
              <Icon name="close" />
            </button>
          )}
        </div>
      </div>
      <RouterPollsCharts routerId={routerId} pollingIntervalSeconds={pollingIntervalSeconds} />
    </div>
  )
}
