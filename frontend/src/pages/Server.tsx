import { useRef, useState } from 'react'
import { apiFetch } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { ChartPanel } from '../components/ChartPanel'
import { RangeSelector } from '../components/RangeSelector'
import { StatCard } from '../components/StatCard'
import { TrafficChart } from '../components/TrafficChart'
import type { ChartRow, ChartSeries } from '../components/TrafficChart'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import { useT } from '../i18n'
import { formatBytes, formatPercent, formatSince } from '../utils/format'
import { withGaps } from '../utils/gaps'

interface ServerPoint {
  t: string
  cpu_percent: number
  mem_used_bytes: number
  mem_total_bytes: number
  disk_used_bytes: number
  disk_total_bytes: number
}

interface ServerHistory {
  bucket_seconds: number
  current: ServerPoint | null
  points: ServerPoint[]
}

const REFRESH_MS = 60_000

const CPU_SERIES: ChartSeries[] = [{ key: 'cpu', label: 'CPU', color: 'var(--series-download)' }]
const percentOf = (used: number, total: number) => (total > 0 ? (used / total) * 100 : 0)

function usage(used: number, total: number): string {
  return `${formatBytes(used)} / ${formatBytes(total)}`
}

export function Server() {
  const [hours, setHours] = useState(24)
  const [history, setHistory] = useState<ServerHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { t } = useT()
  const MEM_SERIES: ChartSeries[] = [{ key: 'mem', label: t('server.memUsed'), color: 'var(--series-clients)' }]
  const DISK_SERIES: ChartSeries[] = [{ key: 'disk', label: t('server.diskUsed'), color: 'var(--series-upload)' }]
  // Only the latest range's history may land (see Dashboard).
  const latestHours = useRef(hours)
  latestHours.current = hours

  useAutoRefresh(
    () => {
      const requested = hours
      apiFetch<ServerHistory>(`/server/history?hours=${requested}`)
        .then((h) => {
          if (latestHours.current !== requested) return
          setHistory(h)
          setError(null)
        })
        .catch(() => setError(t('server.historyError')))
    },
    REFRESH_MS,
    [hours],
  )

  const current = history?.current ?? null
  const maxGap = 2.5 * (history?.bucket_seconds ?? 300)
  const points: ChartRow[] = (history?.points ?? []).map((p) => ({
    t: Date.parse(p.t),
    cpu: p.cpu_percent,
    mem: p.mem_used_bytes,
    disk: p.disk_used_bytes,
  }))
  const rows = withGaps(points, maxGap, (t) => ({ t, cpu: null, mem: null, disk: null }))
  // Memory and disk are drawn in bytes against the whole size, so the axis
  // reads as "how full" and the tooltip gives the GB.
  const memTotal = Math.max(0, ...(history?.points ?? []).map((p) => p.mem_total_bytes))
  const diskTotal = Math.max(0, ...(history?.points ?? []).map((p) => p.disk_total_bytes))

  return (
    <>
      <PageHeader title={t('nav.server')}>
        <RangeSelector value={hours} onChange={setHours} />
      </PageHeader>
      {error && <p className="error">{error}</p>}

      <div className="stat-grid">
        <StatCard
          label="CPU"
          value={current ? formatPercent(current.cpu_percent) : '—'}
          sub={current ? t('resources.read', { since: formatSince(current.t) }) : t('server.noMeasurements')}
        />
        <StatCard
          label={t('resources.memory')}
          value={current ? formatPercent(percentOf(current.mem_used_bytes, current.mem_total_bytes)) : '—'}
          sub={current ? usage(current.mem_used_bytes, current.mem_total_bytes) : undefined}
        />
        <StatCard
          label={t('resources.disk')}
          value={current ? formatPercent(percentOf(current.disk_used_bytes, current.disk_total_bytes)) : '—'}
          sub={current ? usage(current.disk_used_bytes, current.disk_total_bytes) : undefined}
        />
      </div>

      <ChartPanel title="CPU">
        {(expanded) => (
          <TrafficChart
            data={rows}
            series={CPU_SERIES}
            format={formatPercent}
            hours={hours}
            height={expanded ? '100%' : undefined}
            yDomain={[0, 100]}
          />
        )}
      </ChartPanel>

      <div className="grid-2">
        <ChartPanel title={t('resources.memory')}>
          {(expanded) => (
            <TrafficChart
              data={rows}
              series={MEM_SERIES}
              format={formatBytes}
              hours={hours}
              height={expanded ? '100%' : 220}
              yDomain={[0, memTotal || 'auto']}
            />
          )}
        </ChartPanel>
        <ChartPanel title={t('resources.disk')}>
          {(expanded) => (
            <TrafficChart
              data={rows}
              series={DISK_SERIES}
              format={formatBytes}
              hours={hours}
              height={expanded ? '100%' : 220}
              yDomain={[0, diskTotal || 'auto']}
            />
          )}
        </ChartPanel>
      </div>
    </>
  )
}
