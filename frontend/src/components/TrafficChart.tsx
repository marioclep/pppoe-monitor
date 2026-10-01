import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useT } from '../i18n'
import { formatChartTime, formatDateTime } from '../utils/format'

export type ChartRow = { t: number } & Record<string, number | null>

export interface ChartSeries {
  key: string
  label: string
  /** A CSS color, usually var(--series-...) so it follows the theme. */
  color: string
  dashed?: boolean
  /** Area with a gradient below the line (default true unless dashed). */
  fill?: boolean
}

interface TrafficChartProps {
  data: ChartRow[]
  series: ChartSeries[]
  format: (value: number) => string
  hours: number
  /** Pixels, or '100%' to fill a parent with a set height (the chart modal). */
  height?: number | '100%'
  /** Y axis range; defaults to fitting the data. */
  yDomain?: [number, number | 'auto']
}

interface TooltipPayload {
  dataKey?: string | number
  value?: number | null
  color?: string
}

function ChartTooltip(props: {
  active?: boolean
  label?: number
  payload?: TooltipPayload[]
  series: ChartSeries[]
  format: (v: number) => string
}) {
  const { active, label, payload, series, format } = props
  if (!active || !payload?.length || label === undefined) return null
  return (
    <div className="chart-tooltip">
      <div className="tooltip-time">{formatDateTime(label)}</div>
      {payload
        .filter((p) => p.value !== null && p.value !== undefined)
        .map((p) => {
          const s = series.find((x) => x.key === p.dataKey)
          return (
            <div key={String(p.dataKey)} className="legend-item">
              <span className="legend-swatch" style={{ background: s?.color }} />
              {s?.label}: <b className="num">{format(Number(p.value))}</b>
            </div>
          )
        })}
    </div>
  )
}

export function ChartLegend({ series }: { series: ChartSeries[] }) {
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.key} className="legend-item" style={{ color: s.color }}>
          <span className={s.dashed ? 'legend-swatch dashed' : 'legend-swatch'} style={{ background: s.color }} />
          <span style={{ color: 'var(--muted)' }}>{s.label}</span>
        </span>
      ))}
    </div>
  )
}

export function TrafficChart({ data, series, format, hours, height = 260, yDomain }: TrafficChartProps) {
  const { t } = useT()
  if (data.length === 0) return <p className="empty">{t('chart.noData')}</p>
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <defs>
          {series.map((s) => (
            <linearGradient key={s.key} id={`fill-${s.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={s.color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={s.color} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={['dataMin', 'dataMax']}
          tickFormatter={(t: number) => formatChartTime(t, hours)}
          stroke="var(--muted)"
          tick={{ fill: 'var(--muted)', fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: 'var(--border)' }}
          minTickGap={40}
        />
        <YAxis
          domain={yDomain}
          tickFormatter={(v: number) => format(v)}
          width={82}
          stroke="var(--muted)"
          tick={{ fill: 'var(--muted)', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
        />
        <Tooltip
          content={(p) => (
            <ChartTooltip
              active={p.active}
              label={p.label as number | undefined}
              payload={p.payload as unknown as TooltipPayload[] | undefined}
              series={series}
              format={format}
            />
          )}
        />
        {series.map((s) =>
          s.dashed || s.fill === false ? (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={1.5}
              strokeDasharray={s.dashed ? '5 4' : undefined}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ) : (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              fill={`url(#fill-${s.key})`}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ),
        )}
      </ComposedChart>
    </ResponsiveContainer>
  )
}
