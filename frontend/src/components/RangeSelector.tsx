import { useT } from '../i18n'

export const RANGES = [
  { hours: 24, label: '24h' },
  { hours: 24 * 7, label: '7d' },
  { hours: 24 * 30, label: '30d' },
  { hours: 24 * 90, label: '90d' },
]

export function RangeSelector({ value, onChange }: { value: number; onChange: (hours: number) => void }) {
  const { t } = useT()
  return (
    <div className="segmented" role="group" aria-label={t('range.label')}>
      {RANGES.map((r) => (
        <button key={r.hours} type="button" aria-pressed={value === r.hours} onClick={() => onChange(r.hours)}>
          {r.label}
        </button>
      ))}
    </div>
  )
}
