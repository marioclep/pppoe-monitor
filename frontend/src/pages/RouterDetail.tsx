import { useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { ChartPanel } from '../components/ChartPanel'
import { ClientsTable } from '../components/ClientsTable'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { RangeSelector } from '../components/RangeSelector'
import { StatCard } from '../components/StatCard'
import { StatusDot } from '../components/StatusDot'
import { ChartLegend, TrafficChart } from '../components/TrafficChart'
import type { ChartRow, ChartSeries } from '../components/TrafficChart'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import { useT } from '../i18n'
import type { MessageKey } from '../i18n'
import { formatBps, formatBytes, formatCount, formatPercent, formatSince } from '../utils/format'
import { withGaps } from '../utils/gaps'
import { clientsSeries, trafficSeries } from '../utils/series'
import { ROUTER_STATUS_KEY, routerStatus } from '../utils/routerStatus'

interface RouterOverview {
  id: number
  name: string
  host: string
  port: number
  enabled: boolean
  last_polled_at: string | null
  polling_interval_seconds: number
  board_name: string | null
  routeros_version: string | null
  uptime_seconds: number | null
  resources_at: string | null
  clients_connected: number
  clients_total: number
  current_rx_bps: number
  current_tx_bps: number
  month_rx_bytes: number
  month_tx_bytes: number
  resources_polled_at: string | null
  cpu_load: number | null
  mem_free_bytes: number | null
  mem_total_bytes: number | null
  hdd_free_bytes: number | null
  hdd_total_bytes: number | null
}

interface HistoryPoint {
  t: string
  rx_bps: number
  tx_bps: number
  clients_connected: number | null
  cpu_load: number | null
  mem_percent: number | null
  hdd_percent: number | null
}

interface RouterHistory {
  bucket_seconds: number
  points: HistoryPoint[]
}

const REFRESH_MS = 60_000

function resourceSeries(t: (key: MessageKey) => string): ChartSeries[] {
  return [
    { key: 'cpu', label: 'CPU', color: 'var(--series-download)', fill: false },
    { key: 'mem', label: t('resources.memory'), color: 'var(--series-clients)', fill: false },
    { key: 'hdd', label: t('resources.disk'), color: 'var(--series-upload)', fill: false },
  ]
}

function usedPercent(free: number | null, total: number | null): number | null {
  return free === null || total === null || total <= 0 ? null : ((total - free) / total) * 100
}

function usage(free: number | null, total: number | null): string | undefined {
  return free === null || total === null ? undefined : `${formatBytes(total - free)} / ${formatBytes(total)}`
}

/** "12 d 4 h", "5 h 20 min", "8 min". */
function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days} d ${hours} h`
  if (hours > 0) return `${hours} h ${minutes} min`
  return `${minutes} min`
}

export function RouterDetail() {
  const { id } = useParams()
  const [hours, setHours] = useState(24)
  const [overview, setOverview] = useState<RouterOverview | null>(null)
  const [history, setHistory] = useState<RouterHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { t } = useT()
  const TRAFFIC_SERIES = trafficSeries(t)
  const CLIENTS_SERIES = clientsSeries(t)
  const RESOURCE_SERIES = resourceSeries(t)
  // Only the latest range's history may land (see Dashboard).
  const latestHours = useRef(hours)
  latestHours.current = hours
  // Uptime is read at the last resources poll: add the time since.
  const [now, setNow] = useState(() => Date.now())

  useAutoRefresh(
    () => {
      const requested = hours
      setNow(Date.now())
      apiFetch<RouterOverview>(`/routers/${id}/overview`)
        .then((o) => {
          setOverview(o)
          setError(null)
        })
        .catch(() => setError(t('router.loadError')))
      apiFetch<RouterHistory>(`/dashboard/routers/${id}/history?hours=${requested}`)
        .then((h) => {
          if (latestHours.current === requested) setHistory(h)
        })
        .catch(() => setError(t('router.historyError')))
    },
    REFRESH_MS,
    [id, hours],
  )

  const interval = overview?.polling_interval_seconds ?? 300
  const maxGap = 2.5 * Math.max(history?.bucket_seconds ?? 300, interval)
  const points: ChartRow[] = (history?.points ?? []).map((p) => ({
    t: Date.parse(p.t),
    download: p.tx_bps,
    upload: p.rx_bps,
    clients: p.clients_connected,
    cpu: p.cpu_load,
    mem: p.mem_percent,
    hdd: p.hdd_percent,
  }))
  const blank = (t: number) => ({ t, download: null, upload: null, clients: null, cpu: null, mem: null, hdd: null })
  const trafficRows = withGaps(points, maxGap, blank)
  const clientRows = withGaps(
    points.filter((p) => p.clients !== null),
    maxGap,
    (t) => ({ t, clients: null }),
  )
  // Polls from before resources were collected have none: leave them out, so
  // the chart starts where the data does instead of with a long gap.
  const resourceRows = withGaps(
    points.filter((p) => p.cpu !== null || p.mem !== null || p.hdd !== null),
    maxGap,
    blank,
  )

  const status = overview ? routerStatus(overview.last_polled_at, interval) : null
  const uptime =
    overview?.uptime_seconds != null && overview.resources_at
      ? overview.uptime_seconds + Math.max(0, (now - Date.parse(overview.resources_at)) / 1000)
      : null
  const memPercent = overview ? usedPercent(overview.mem_free_bytes, overview.mem_total_bytes) : null
  const hddPercent = overview ? usedPercent(overview.hdd_free_bytes, overview.hdd_total_bytes) : null
  const noResources = t('resources.noData')

  return (
    <>
      <p style={{ margin: 0 }}>
        <Link to="/dashboard" className="legend-item">
          <Icon name="back" /> {t('nav.dashboard')}
        </Link>
      </p>
      <PageHeader title={overview ? overview.name : `Router #${id}`}>
        <RangeSelector value={hours} onChange={setHours} />
      </PageHeader>
      {overview && (
        <p className="muted router-facts">
          {overview.enabled && status ? (
            <StatusDot tone={status} label={t(ROUTER_STATUS_KEY[status])} showLabel />
          ) : (
            <StatusDot tone="off" label={t('status.disabled')} showLabel />
          )}
          <span className="num">
            {overview.host}:{overview.port}
          </span>
          {overview.board_name && <span>{overview.board_name}</span>}
          {overview.routeros_version && <span>RouterOS {overview.routeros_version}</span>}
          {uptime !== null && <span>{t('router.uptime', { duration: formatDuration(uptime) })}</span>}
          <span>{t('router.lastPoll', { since: formatSince(overview.last_polled_at) })}</span>
        </p>
      )}
      {error && <p className="error">{error}</p>}

      {/* Row 1: the router itself; row 2: its traffic. */}
      <div className="stat-grid stat-grid-4">
        <StatCard
          label="CPU"
          value={overview?.cpu_load != null ? formatPercent(overview.cpu_load) : '—'}
          sub={overview?.resources_polled_at ? t('resources.read', { since: formatSince(overview.resources_polled_at) }) : noResources}
        />
        <StatCard
          label={t('resources.memory')}
          value={memPercent !== null ? formatPercent(memPercent) : '—'}
          sub={overview ? (usage(overview.mem_free_bytes, overview.mem_total_bytes) ?? noResources) : undefined}
        />
        <StatCard
          label={t('resources.disk')}
          value={hddPercent !== null ? formatPercent(hddPercent) : '—'}
          sub={overview ? (usage(overview.hdd_free_bytes, overview.hdd_total_bytes) ?? noResources) : undefined}
        />
        <StatCard
          label={t('chart.connectedClients')}
          value={overview ? formatCount(overview.clients_connected) : '—'}
          sub={overview ? t('router.ofClients', { n: formatCount(overview.clients_total) }) : undefined}
        />
      </div>
      <div className="stat-grid stat-grid-4">
        <StatCard
          label={t('dashboard.downloadNow')}
          value={overview ? <span className="series-download">{formatBps(overview.current_tx_bps)}</span> : '—'}
        />
        <StatCard
          label={t('dashboard.uploadNow')}
          value={overview ? <span className="series-upload">{formatBps(overview.current_rx_bps)}</span> : '—'}
        />
        <StatCard
          label={t('client.monthDownload')}
          value={overview ? <span className="series-download">{formatBytes(overview.month_tx_bytes)}</span> : '—'}
        />
        <StatCard
          label={t('client.monthUpload')}
          value={overview ? <span className="series-upload">{formatBytes(overview.month_rx_bytes)}</span> : '—'}
        />
      </div>

      <ChartPanel title={t('chart.traffic')} action={<ChartLegend series={TRAFFIC_SERIES} />}>
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
        <ChartPanel title={t('resources.title')} action={<ChartLegend series={RESOURCE_SERIES} />}>
          {(expanded) => (
            <TrafficChart
              data={resourceRows}
              series={RESOURCE_SERIES}
              format={formatPercent}
              hours={hours}
              height={expanded ? '100%' : 220}
              yDomain={[0, 100]}
            />
          )}
        </ChartPanel>
      </div>

      <ClientsTable routerId={Number(id)} />
    </>
  )
}
