import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { useT } from '../i18n'
import { DIRECTION_KEY, formatGb } from '../utils/alerts'
import { formatDateTime } from '../utils/format'
import type { Direction } from '../utils/alerts'

interface AlertEvent {
  id: number
  client_id: number
  client_username: string
  router_id: number
  router_name: string
  direction: Direction
  threshold_bytes: number
  triggered_at: string
  accumulated_bytes_at_trigger: number
}

/** The latest alerts. Recorded even without email or Telegram set up. */
export function Alerts() {
  const [events, setEvents] = useState<AlertEvent[] | null>(null)
  const [error, setError] = useState(false)
  const { t } = useT()

  useEffect(() => {
    apiFetch<AlertEvent[]>('/alerts/events')
      .then(setEvents)
      .catch(() => setError(true))
  }, [])

  return (
    <>
      <PageHeader title={t('nav.alerts')} />
      <p className="muted">
        {t('alerts.intro')} <Link to="/settings">{t('alerts.whereToConfigure')}</Link>
      </p>
      {error && <p className="error">{t('alerts.loadError')}</p>}
      <Panel>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('alerts.date')}</th>
                <th>{t('common.client')}</th>
                <th>{t('common.router')}</th>
                <th>{t('thresholds.direction')}</th>
                <th>{t('alerts.usageAtAlert')}</th>
                <th>{t('thresholds.threshold')}</th>
              </tr>
            </thead>
            <tbody>
              {(events ?? []).map((e) => (
                <tr key={e.id}>
                  <td data-label={t('alerts.date')}>{formatDateTime(e.triggered_at)}</td>
                  <td data-label={t('common.client')}>
                    <Link to={`/clients/${e.client_id}`}>{e.client_username}</Link>
                  </td>
                  <td data-label={t('common.router')}>
                    <Link to={`/routers/${e.router_id}`}>{e.router_name}</Link>
                  </td>
                  <td data-label={t('thresholds.direction')}>
                    <span className={e.direction === 'download' ? 'series-download' : 'series-upload'}>
                      {t(DIRECTION_KEY[e.direction])}
                    </span>
                  </td>
                  <td data-label={t('alerts.usageAtAlert')} className="num">
                    {formatGb(e.accumulated_bytes_at_trigger)}
                  </td>
                  <td data-label={t('thresholds.threshold')} className="num">
                    {formatGb(e.threshold_bytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {events?.length === 0 && <p className="empty">{t('alerts.none')}</p>}
      </Panel>
    </>
  )
}
