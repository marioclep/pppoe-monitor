export type StatusTone = 'ok' | 'late' | 'down' | 'off'

export function StatusDot({ tone, label, showLabel = false }: { tone: StatusTone; label: string; showLabel?: boolean }) {
  return (
    <span className="status" title={label}>
      <span className={`status-dot ${tone}`} aria-hidden="true" />
      {showLabel ? label : <span className="visually-hidden">{label}</span>}
    </span>
  )
}
