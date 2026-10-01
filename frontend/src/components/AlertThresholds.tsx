import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { useT } from '../i18n'
import { CHANNEL_LABEL, DIRECTIONS, GB, formatGb } from '../utils/alerts'
import type { Direction, Threshold } from '../utils/alerts'


interface RowProps {
  direction: Direction
  clientId: number | null
  own: Threshold | undefined
  fallback: Threshold | undefined
  onChanged: () => void
}

function ThresholdRow({ direction, clientId, own, fallback, onChanged }: RowProps) {
  const { canEdit } = useAuth()
  const { t } = useT()
  const describe = (threshold: Threshold) =>
    t('thresholds.describe', {
      value: formatGb(threshold.bytes_threshold),
      channel: CHANNEL_LABEL[threshold.notify_channel] ?? threshold.notify_channel,
    })
  const [gb, setGb] = useState(own ? String(own.bytes_threshold / GB) : '')
  const [channel, setChannel] = useState(own?.notify_channel ?? 'email')
  const [error, setError] = useState<string | null>(null)

  async function save(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const value = Number(gb.replace(',', '.'))
    if (!(value > 0)) {
      setError(t('thresholds.mustBePositive'))
      return
    }
    try {
      await apiFetch('/alerts/thresholds', {
        method: 'POST',
        body: JSON.stringify({
          client_id: clientId,
          direction,
          bytes_threshold: Math.round(value * GB),
          notify_channel: channel,
        }),
      })
      onChanged()
    } catch {
      setError(t('thresholds.saveError'))
    }
  }

  async function remove() {
    if (!own) return
    setError(null)
    try {
      await apiFetch(`/alerts/thresholds/${own.id}`, { method: 'DELETE' })
      onChanged()
    } catch {
      setError(t('thresholds.removeError'))
    }
  }

  let current: string
  if (own) current = describe(own)
  else if (clientId !== null && fallback) current = t('thresholds.usesGlobal', { value: describe(fallback) })
  else current = t('thresholds.none')

  return (
    <div className="threshold-row">
      <div className="threshold-current">
        <strong>{t(direction === 'download' ? 'client.monthDownload' : 'client.monthUpload')}</strong>
        <span className="muted">{current}</span>
      </div>
      {canEdit && (
        <form className="inline" onSubmit={save}>
          <label>
            {t('thresholds.gb')}
            <input
              inputMode="decimal"
              value={gb}
              placeholder={fallback && clientId !== null ? String(fallback.bytes_threshold / GB) : t('thresholds.example')}
              onChange={(e) => setGb(e.target.value.replace(/[^\d.,]/g, ''))}
            />
          </label>
          <label>
            {t('thresholds.channel')}
            <select value={channel} onChange={(e) => setChannel(e.target.value)}>
              <option value="email">Email</option>
              <option value="telegram">Telegram</option>
            </select>
          </label>
          <button type="submit">{t('common.save')}</button>
          {own && (
            <button type="button" className="secondary" onClick={remove}>
              {t('thresholds.remove')}
            </button>
          )}
        </form>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  )
}

/** Download and upload thresholds of one client (clientId) or the global
 *  ones (null). A client without its own uses the global one. */
export function AlertThresholds({ clientId, onChanged }: { clientId: number | null; onChanged?: () => void }) {
  const [thresholds, setThresholds] = useState<Threshold[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const { t } = useT()

  const reload = useCallback(() => {
    apiFetch<Threshold[]>('/alerts/thresholds')
      .then(setThresholds)
      .catch(() => setLoadError(true))
  }, [])

  useEffect(reload, [reload])

  if (loadError) return <p className="error">{t('thresholds.loadError')}</p>
  if (!thresholds) return <p className="muted">{t('common.loading')}</p>

  const find = (id: number | null, direction: Direction) =>
    thresholds.find((th) => th.client_id === id && th.direction === direction)

  return (
    <div className="thresholds">
      {DIRECTIONS.map((direction) => {
        const own = find(clientId, direction)
        return (
          <ThresholdRow
            // Remount when the stored threshold changes, so the inputs start from it.
            key={`${direction}-${own?.id}-${own?.bytes_threshold}-${own?.notify_channel}`}
            direction={direction}
            clientId={clientId}
            own={own}
            fallback={clientId !== null ? find(null, direction) : undefined}
            onChanged={() => {
              reload()
              onChanged?.()
            }}
          />
        )
      })}
    </div>
  )
}
