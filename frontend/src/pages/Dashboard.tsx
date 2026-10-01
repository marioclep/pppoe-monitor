import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { RangeSelector, RANGES } from '../components/RangeSelector'
import { ChartModal } from '../components/ChartModal'
import { ChartPanel } from '../components/ChartPanel'
import { RouterPollsCharts, RouterPollsPopover, RouterPollsTitle } from '../components/RouterPollsPopover'
import { StatCard } from '../components/StatCard'
import { StatusDot } from '../components/StatusDot'
import { ChartLegend, TrafficChart } from '../components/TrafficChart'
import type { ChartRow } from '../components/TrafficChart'
import { useSite } from '../context/SiteContext'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import { useT } from '../i18n'
import { formatBps, formatBytes, formatCount, formatSince } from '../utils/format'
import { withGaps } from '../utils/gaps'
import { clientsSeries, trafficSeries } from '../utils/series'
import { ROUTER_STATUS_KEY, routerStatus } from '../utils/routerStatus'

interface RouterSummary {
  router_id: number
  router_name: string
  clients_connected: number
  current_rx_bps: number
  current_tx_bps: number
  last_polled_at: string | null
}

interface DashboardSummary {
  total_clients_connected: number
  current_rx_bps: number
  current_tx_bps: number
  by_router: RouterSummary[]
  polling_interval_seconds: number
  clients_seen_this_period: number
}

interface HistoryPoint {
  t: string
  rx_bps: number
  tx_bps: number
  clients_connected: number | null
}

interface DashboardHistory {
  bucket_seconds: number
  points: HistoryPoint[]
}

interface TopClient {
  id: number
  username: string
  router_name: string
  accumulated_tx_bytes: number
}

const REFRESH_MS = 60_000

const HOVER_DELAY_MS = 500
// Time to move the pointer from the row onto the popover before it closes.
const LEAVE_DELAY_MS = 250

interface PollsPopover {
  routerId: number
  routerName: string
  anchor: DOMRect
  pinned: boolean
}

// Touch screens fire mouseenter on tap: only real pointers open on hover.
const canHover = () => window.matchMedia('(hover: hover)').matches

export function Dashboard() {
  const { siteName } = useSite()
  const { t } = useT()
  const TRAFFIC_SERIES = trafficSeries(t)
  const CLIENTS_SERIES = clientsSeries(t)
  const [hours, setHours] = useState(24)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [history, setHistory] = useState<DashboardHistory | null>(null)
  const [top, setTop] = useState<TopClient[]>([])
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const [popover, setPopover] = useState<PollsPopover | null>(null)
  const [expandedRouter, setExpandedRouter] = useState<{ id: number; name: string } | null>(null)
  const hoverTimer = useRef<number | undefined>(undefined)
  const leaveTimer = useRef<number | undefined>(undefined)
  const closePopover = useCallback(() => setPopover(null), [])
  // Only the latest range's history may land: a slow 90d response arriving
  // after a click on 24h must not be drawn under the 24h label.
  const latestHours = useRef(hours)
  latestHours.current = hours

  useAutoRefresh(
    () => {
      const requested = hours
      apiFetch<DashboardSummary>('/dashboard/summary')
        .then((s) => {
          setSummary(s)
          setError(null)
        })
        .catch(() => setError(t('dashboard.summaryError')))
      apiFetch<DashboardHistory>(`/dashboard/history?hours=${requested}`)
        .then((h) => {
          if (latestHours.current === requested) setHistory(h)
        })
        .catch(() => setError(t('dashboard.historyError')))
      apiFetch<{ items: TopClient[] }>('/clients?sort_by=download&dir=desc&page_size=10')
        .then((page) => setTop(page.items))
        .catch(() => setError(t('dashboard.topError')))
    },
    REFRESH_MS,
    [hours],
  )

  useEffect(
    () => () => {
      window.clearTimeout(hoverTimer.current)
      window.clearTimeout(leaveTimer.current)
    },
    [],
  )
  // A popover opened by a tap closes on the next tap anywhere else.
  useEffect(() => {
    if (!popover?.pinned) return
    document.addEventListener('click', closePopover)
    return () => document.removeEventListener('click', closePopover)
  }, [popover?.pinned, closePopover])

  function hoverRouter(r: RouterSummary, row: HTMLElement) {
    if (!canHover() || popover?.pinned) return
    window.clearTimeout(hoverTimer.current)
    window.clearTimeout(leaveTimer.current)
    // Already showing this router (the pointer came back from the popover).
    if (popover?.routerId === r.router_id) return
    hoverTimer.current = window.setTimeout(
      () =>
        setPopover({
          routerId: r.router_id,
          routerName: r.router_name,
          anchor: row.getBoundingClientRect(),
          pinned: false,
        }),
      HOVER_DELAY_MS,
    )
  }

  function leaveRouter() {
    window.clearTimeout(hoverTimer.current)
    window.clearTimeout(leaveTimer.current)
    leaveTimer.current = window.setTimeout(() => setPopover((p) => (p?.pinned ? p : null)), LEAVE_DELAY_MS)
  }

  function expandRouter() {
    if (!popover) return
    window.clearTimeout(leaveTimer.current)
    setExpandedRouter({ id: popover.routerId, name: popover.routerName })
    setPopover(null)
  }

  function tapChart(r: RouterSummary, button: HTMLElement) {
    window.clearTimeout(hoverTimer.current)
    setPopover((p) =>
      p?.pinned && p.routerId === r.router_id
        ? null
        : { routerId: r.router_id, routerName: r.router_name, anchor: button.getBoundingClientRect(), pinned: true },
    )
  }

  const rangeLabel = RANGES.find((r) => r.hours === hours)?.label ?? ''
  // A gap is more than 2.5 buckets -- or 2.5 polls, when polling is slower
  // than the bucket (e.g. every 15 min on the 5-minute 24h chart).
  const maxGap = 2.5 * Math.max(history?.bucket_seconds ?? 300, summary?.polling_interval_seconds ?? 0)
  const points: ChartRow[] = (history?.points ?? []).map((p) => ({
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
  const peakDownload = Math.max(0, ...points.map((p) => p.download ?? 0))
  const peakUpload = Math.max(0, ...points.map((p) => p.upload ?? 0))

  const interval = summary?.polling_interval_seconds ?? 300
  const statuses = (summary?.by_router ?? []).map((r) => routerStatus(r.last_polled_at, interval))
  const okCount = statuses.filter((s) => s === 'ok').length
  const routerCount = statuses.length
  const topMax = Math.max(1, ...top.map((c) => c.accumulated_tx_bytes))

  return (
    <>
      <PageHeader title={siteName ? `${t('nav.dashboard')} ${siteName}` : t('nav.dashboard')}>
        <RangeSelector value={hours} onChange={setHours} />
      </PageHeader>
      {error && <p className="error">{error}</p>}

      <div className="stat-grid">
        <StatCard
          label={t('common.connected')}
          value={summary ? formatCount(summary.total_clients_connected) : '—'}
          sub={summary ? t('dashboard.seenThisMonth', { n: formatCount(summary.clients_seen_this_period) }) : undefined}
        />
        <StatCard
          label={t('dashboard.downloadNow')}
          value={summary ? formatBps(summary.current_tx_bps) : '—'}
          sub={points.length ? t('dashboard.peak', { range: rangeLabel, value: formatBps(peakDownload) }) : undefined}
        />
        <StatCard
          label={t('dashboard.uploadNow')}
          value={summary ? formatBps(summary.current_rx_bps) : '—'}
          sub={points.length ? t('dashboard.peak', { range: rangeLabel, value: formatBps(peakUpload) }) : undefined}
        />
        <StatCard
          label={t('nav.routers')}
          value={summary ? `${okCount} / ${routerCount}` : '—'}
          sub={
            summary
              ? okCount === routerCount
                ? t('dashboard.allUpToDate')
                : t('dashboard.withProblems', { n: routerCount - okCount })
              : undefined
          }
        />
      </div>

      <ChartPanel title={t('dashboard.totalTraffic')} action={<ChartLegend series={TRAFFIC_SERIES} />}>
        {(expanded) => (
          <TrafficChart
            data={trafficRows}
            series={TRAFFIC_SERIES}
            format={formatBps}
            hours={hours}
            height={expanded ? '100%' : 280}
          />
        )}
      </ChartPanel>

      <div className="grid-2">
        <ChartPanel title={t('chart.connectedClients')}>
          {(expanded) => (
            <TrafficChart
              data={clientRows}
              series={CLIENTS_SERIES}
              format={formatCount}
              hours={hours}
              height={expanded ? '100%' : 220}
            />
          )}
        </ChartPanel>

        <Panel title={t('dashboard.byRouter')}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t('common.router')}</th>
                  <th>{t('common.connected')}</th>
                  <th>{t('common.download')}</th>
                  <th>{t('common.lastPoll')}</th>
                </tr>
              </thead>
              <tbody>
                {(summary?.by_router ?? []).map((r, i) => (
                  <tr
                    key={r.router_id}
                    className="clickable"
                    onClick={() => navigate(`/routers/${r.router_id}`)}
                    onMouseEnter={(e) => hoverRouter(r, e.currentTarget)}
                    onMouseLeave={leaveRouter}
                  >
                    <td data-label={t('common.router')}>
                      <StatusDot tone={statuses[i]} label={t(ROUTER_STATUS_KEY[statuses[i]])} /> {r.router_name}
                      <button
                        type="button"
                        className="icon-button row-chart-button"
                        aria-label={t('polls.viewOf', { name: r.router_name })}
                        onClick={(e) => {
                          e.stopPropagation()
                          tapChart(r, e.currentTarget)
                        }}
                      >
                        <Icon name="chart" />
                      </button>
                    </td>
                    <td data-label={t('common.connected')}>{formatCount(r.clients_connected)}</td>
                    <td data-label={t('common.download')}>{formatBps(r.current_tx_bps)}</td>
                    <td data-label={t('common.lastPoll')} className={statuses[i] === 'ok' ? 'muted' : undefined}>
                      {formatSince(r.last_polled_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {summary && summary.by_router.length === 0 && <p className="empty">{t('dashboard.noRouters')}</p>}
          {popover && (
            <RouterPollsPopover
              key={popover.routerId}
              routerId={popover.routerId}
              routerName={popover.routerName}
              anchor={popover.anchor}
              pollingIntervalSeconds={interval}
              pinned={popover.pinned}
              onClose={closePopover}
              onExpand={expandRouter}
              onMouseEnter={() => window.clearTimeout(leaveTimer.current)}
              onMouseLeave={leaveRouter}
            />
          )}
          {expandedRouter && (
            <ChartModal title={<RouterPollsTitle routerName={expandedRouter.name} />} onClose={() => setExpandedRouter(null)}>
              <RouterPollsCharts routerId={expandedRouter.id} pollingIntervalSeconds={interval} expanded />
            </ChartModal>
          )}
        </Panel>
      </div>

      <Panel title={t('dashboard.topTitle')} action={<Link to="/clients">{t('dashboard.viewAll')}</Link>}>
        <div className="table-wrap">
          <table>
            <tbody>
              {top.map((c, i) => (
                <tr key={c.id} className="clickable" onClick={() => navigate(`/clients/${c.id}`)}>
                  <td className="rank num">{i + 1}</td>
                  <td data-label={t('common.client')}>
                    <Link to={`/clients/${c.id}`} onClick={(e) => e.stopPropagation()}>
                      {c.username}
                    </Link>{' '}
                    <span className="muted">· {c.router_name}</span>
                  </td>
                  <td style={{ width: '40%' }}>
                    <div className="bar">
                      <span style={{ width: `${(c.accumulated_tx_bytes / topMax) * 100}%` }} />
                    </div>
                  </td>
                  <td data-label={t('common.download')} className="num" style={{ textAlign: 'right' }}>
                    {formatBytes(c.accumulated_tx_bytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {top.length === 0 && <p className="empty">{t('dashboard.noUsage')}</p>}
      </Panel>
    </>
  )
}
