import type { ChartSeries } from '../components/TrafficChart'
import type { MessageKey } from '../i18n/core'

type Translate = (key: MessageKey) => string

// PPPoE-server interface: TX = what the router sends the clients (their download).
export function trafficSeries(t: Translate): ChartSeries[] {
  return [
    { key: 'download', label: t('common.download'), color: 'var(--series-download)' },
    { key: 'upload', label: t('common.upload'), color: 'var(--series-upload)' },
  ]
}

export function clientsSeries(t: Translate): ChartSeries[] {
  return [{ key: 'clients', label: t('common.connected'), color: 'var(--series-clients)' }]
}
