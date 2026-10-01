import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { AlertThresholds } from '../components/AlertThresholds'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { ChartPanel } from '../components/ChartPanel'
import { RangeSelector } from '../components/RangeSelector'
import { StatCard } from '../components/StatCard'
import { StatusDot } from '../components/StatusDot'
import { ChartLegend, TrafficChart } from '../components/TrafficChart'
import type { ChartRow, ChartSeries } from '../components/TrafficChart'
import { useT } from '../i18n'
import type { MessageKey } from '../i18n'
import { formatBps, formatBytes } from '../utils/format'
import { withGaps } from '../utils/gaps'
import { trafficSeries } from '../utils/series'

// On a PPPoE-server interface TX is what the router sends the client (its
// download) and RX is its upload.
interface HistoryPoint {
  sampled_at: string
  rx_bytes_delta: number
  tx_bytes_delta: number
  rx_bps: number
  tx_bps: number
  // Only on hourly points (30/90-day ranges): the fastest 5-minute sample.
  peak_rx_bps: number | null
  peak_tx_bps: number | null
}

interface ClientRow {
  id: number
  router_name: string
  username: string
  is_active: boolean
  current_rx_bps: number
  current_tx_bps: number
  accumulated_rx_bytes: number
  accumulated_tx_bytes: number
  addresses: string[]
  last_address: string | null
  last_mac: string | null
}

const HOUR_SECONDS = 3600

function peakSeries(t: (key: MessageKey) => string): ChartSeries[] {
  return [
    { key: 'peakDownload', label: t('client.peakDownload'), color: 'var(--series-download)', dashed: true },
    { key: 'peakUpload', label: t('client.peakUpload'), color: 'var(--series-upload)', dashed: true },
  ]
}

// Running total since the start of the selected range: its slope is the
// speed chart. Not broken at gaps: a flat total across a gap is correct.
function toCumulative(points: HistoryPoint[]): ChartRow[] {
  const rows: ChartRow[] = []
  let download = 0
  let upload = 0
  for (const p of points) {
    download += p.tx_bytes_delta
    upload += p.rx_bytes_delta
    rows.push({ t: Date.parse(p.sampled_at), download, upload })
  }
  return rows
}

export function ClientDetail() {
  const { id } = useParams<{ id: string }>()
  const [hours, setHours] = useState(24)
  const [points, setPoints] = useState<HistoryPoint[]>([])
  const [client, setClient] = useState<ClientRow | null>(null)
  const [pollSeconds, setPollSeconds] = useState(300)
  const [error, setError] = useState<string | null>(null)
  const { t } = useT()
  const SPEED_SERIES = trafficSeries(t)

  useEffect(() => {
    if (!id) return
    apiFetch<HistoryPoint[]>(`/clients/${id}/history?hours=${hours}`)
      .then((p) => {
        setPoints(p)
        setError(null)
      })
      .catch(() => setError(t('client.historyError')))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- t only changes the error text
  }, [id, hours])

  useEffect(() => {
    if (!id) return
    apiFetch<ClientRow>(`/clients/${id}`)
      .then(setClient)
      .catch(() => {
        /* header falls back to showing the client id */
      })
  }, [id])

  useEffect(() => {
    apiFetch<{ polling_interval_seconds: number }>('/dashboard/summary')
      .then((s) => setPollSeconds(s.polling_interval_seconds))
      .catch(() => {
        /* keep the 5-minute default for gap detection */
      })
  }, [])

  // Long ranges come as one point per hour: the hour's average speed plus
  // its peak, drawn dashed.
  const hourly = points.some((p) => p.peak_tx_bps !== null)
  const maxGap = 2.5 * (hourly ? HOUR_SECONDS : pollSeconds)
  const speedRows = withGaps(
    points.map((p): ChartRow => ({
      t: Date.parse(p.sampled_at),
      download: p.tx_bps,
      upload: p.rx_bps,
      peakDownload: p.peak_tx_bps,
      peakUpload: p.peak_rx_bps,
    })),
    maxGap,
    (t) => ({ t, download: null, upload: null, peakDownload: null, peakUpload: null }),
  )
  const speedSeries = hourly ? [...SPEED_SERIES, ...peakSeries(t)] : SPEED_SERIES

  return (
    <>
      <p style={{ margin: 0 }}>
        <Link to="/clients" className="legend-item">
          <Icon name="back" /> {t('nav.clients')}
        </Link>
      </p>
      <PageHeader title={client ? client.username : t('client.fallbackTitle', { id: id ?? '' })}>
        {client && (
          <span className="muted">
            <StatusDot
              tone={client.is_active ? 'ok' : 'off'}
              label={client.is_active ? t('status.connected') : t('status.disconnected')}
              showLabel
            />{' '}
            · {client.router_name}
            {client.addresses.length > 0
              ? ` · ${client.addresses.join(' · ')}`
              : client.last_address
                ? ` · ${client.last_address} (${t('clients.lastIp')})`
                : ''}
            {client.last_mac && ` · ${client.last_mac}`}
          </span>
        )}
      </PageHeader>

      {client && (
        <div className="stat-grid stat-grid-4">
          <StatCard
            label={t('client.currentDownload')}
            value={
              client.is_active ? (
                <span className="series-download">{formatBps(client.current_tx_bps)}</span>
              ) : (
                t('status.disconnected')
              )
            }
          />
          <StatCard
            label={t('client.currentUpload')}
            value={
              client.is_active ? (
                <span className="series-upload">{formatBps(client.current_rx_bps)}</span>
              ) : (
                t('status.disconnected')
              )
            }
          />
          <StatCard
            label={t('client.monthDownload')}
            value={<span className="series-download">{formatBytes(client.accumulated_tx_bytes)}</span>}
          />
          <StatCard
            label={t('client.monthUpload')}
            value={<span className="series-upload">{formatBytes(client.accumulated_rx_bytes)}</span>}
          />
        </div>
      )}

      <div className="controls">
        <RangeSelector value={hours} onChange={setHours} />
      </div>
      {error && <p className="error">{error}</p>}

      <ChartPanel
        title={hourly ? t('client.speedHourly') : t('client.speedPerPoll')}
        action={<ChartLegend series={speedSeries} />}
      >
        {(expanded) => (
          <TrafficChart
            data={error ? [] : speedRows}
            series={speedSeries}
            format={formatBps}
            hours={hours}
            height={expanded ? '100%' : undefined}
          />
        )}
      </ChartPanel>

      <ChartPanel title={t('client.accumulated')} action={<ChartLegend series={SPEED_SERIES} />}>
        {(expanded) => (
          <TrafficChart
            data={error ? [] : toCumulative(points)}
            series={SPEED_SERIES}
            format={formatBytes}
            hours={hours}
            height={expanded ? '100%' : undefined}
          />
        )}
      </ChartPanel>

      {client && (
        <Panel title={t('client.alertsTitle')}>
          <AlertThresholds clientId={client.id} />
        </Panel>
      )}
    </>
  )
}
