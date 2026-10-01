import { Fragment, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { Panel } from './Panel'
import { StatusDot } from './StatusDot'
import { useT } from '../i18n'
import type { MessageKey } from '../i18n'
import { formatBps, formatBytes, formatCount } from '../utils/format'

interface ClientRow {
  id: number
  router_id: number
  router_name: string
  username: string
  is_active: boolean
  accumulated_rx_bytes: number
  accumulated_tx_bytes: number
  current_rx_bps: number
  current_tx_bps: number
  addresses: string[]
  last_address: string | null
}

interface ClientPage {
  items: ClientRow[]
  total: number
  page: number
  page_size: number
}

interface RouterOption {
  id: number
  name: string
}

// Sort keys accepted by GET /clients (sorting and paging happen server side).
type SortKey = 'username' | 'router' | 'status' | 'current' | 'download' | 'upload'

const COLUMNS: { key: SortKey; label: MessageKey }[] = [
  { key: 'username', label: 'common.user' },
  { key: 'router', label: 'common.router' },
  { key: 'status', label: 'clients.status' },
  { key: 'current', label: 'clients.currentTraffic' },
  // PPPoE-server interface: TX = the client's download, RX = its upload.
  { key: 'download', label: 'clients.accumulatedDownload' },
  { key: 'upload', label: 'clients.accumulatedUpload' },
]

const PAGE_SIZES = [25, 50, 100]

/** Live sessions' IPs; offline, the last known one (muted). */
function ClientAddress({ client }: { client: ClientRow }) {
  const { t } = useT()
  if (client.addresses.length > 0) return <>{client.addresses.join(' · ')}</>
  if (client.last_address) return <span className="muted">{client.last_address} ({t('clients.lastIp')})</span>
  return <span className="muted">—</span>
}
const DEFAULTS = { sort: 'download', dir: 'desc', page: '1', size: '50' }
const SEARCH_DEBOUNCE_MS = 300

/** The paged, sortable, searchable clients table. With `routerId` it lists
 *  only that router's clients, without the router filter and column. */
export function ClientsTable({ routerId: fixedRouterId }: { routerId?: number }) {
  // Filters, sort and page live in the URL, so returning from a client's
  // detail page (or sharing the link) restores the same view.
  const [params, setParams] = useSearchParams()
  const sort = (params.get('sort') ?? DEFAULTS.sort) as SortKey
  const dir = params.get('dir') ?? DEFAULTS.dir
  const page = Number(params.get('page') ?? DEFAULTS.page)
  const size = Number(params.get('size') ?? DEFAULTS.size)
  const q = params.get('q') ?? ''
  const routerId = fixedRouterId !== undefined ? String(fixedRouterId) : (params.get('router') ?? '')
  const activeOnly = params.get('active') === '1'

  const [data, setData] = useState<ClientPage | null>(null)
  const [routers, setRouters] = useState<RouterOption[]>([])
  const [error, setError] = useState<string | null>(null)
  const [searchText, setSearchText] = useState(q)
  const navigate = useNavigate()
  const { t } = useT()

  // Update URL params; any change other than the page itself goes back to page 1.
  function update(changes: Record<string, string | null>) {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(changes)) {
          if (value === null || value === '') next.delete(key)
          else next.set(key, value)
        }
        if (!('page' in changes)) next.delete('page')
        return next
      },
      { replace: !('page' in changes) },
    )
  }

  useEffect(() => {
    if (fixedRouterId !== undefined) return
    apiFetch<RouterOption[]>('/routers')
      .then(setRouters)
      .catch(() => {
        /* the router filter just stays empty */
      })
  }, [fixedRouterId])

  useEffect(() => {
    if (searchText === q) return
    const timer = setTimeout(() => update({ q: searchText.trim() }), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to typing
  }, [searchText])

  useEffect(() => {
    const query = new URLSearchParams({ sort_by: sort, dir, page: String(page), page_size: String(size) })
    if (q) query.set('q', q)
    if (routerId) query.set('router_id', routerId)
    if (activeOnly) query.set('active_only', 'true')
    let cancelled = false
    apiFetch<ClientPage>(`/clients?${query}`)
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      })
      .catch(() => {
        if (!cancelled) setError(t('clients.loadError'))
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- t only changes the error text
  }, [sort, dir, page, size, q, routerId, activeOnly])

  function toggleSort(key: SortKey) {
    if (key === sort) update({ dir: dir === 'asc' ? 'desc' : 'asc' })
    else update({ sort: key, dir: key === 'username' || key === 'router' ? 'asc' : 'desc' })
  }

  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / size))
  const first = total === 0 ? 0 : (page - 1) * size + 1
  const last = Math.min(page * size, total)

  const columns = fixedRouterId === undefined ? COLUMNS : COLUMNS.filter((col) => col.key !== 'router')

  return (
    <Panel title={fixedRouterId === undefined ? undefined : t('clients.pppoeTitle')}>
        <div className="controls" style={{ marginBottom: 12 }}>
          <input
            type="search"
            placeholder={t('clients.search')}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          {fixedRouterId === undefined && (
            <select value={routerId} onChange={(e) => update({ router: e.target.value })}>
              <option value="">{t('clients.allRouters')}</option>
              {routers.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          )}
          <label className="checkbox">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(e) => update({ active: e.target.checked ? '1' : null })}
            />
            {t('clients.onlyConnected')}
          </label>
        </div>

        {error && <p className="error">{error}</p>}

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {columns.map((col) => (
                  <Fragment key={col.key}>
                    <th className="sortable" onClick={() => toggleSort(col.key)}>
                      {t(col.label)}
                      {sort === col.key ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
                    {col.key === 'username' && <th>IP</th>}
                  </Fragment>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.items.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => navigate(`/clients/${c.id}`)}>
                  <td data-label={t('common.user')}>
                    <Link to={`/clients/${c.id}`} onClick={(e) => e.stopPropagation()}>
                      {c.username}
                    </Link>
                  </td>
                  <td data-label="IP" className="num">
                    <ClientAddress client={c} />
                  </td>
                  {fixedRouterId === undefined && <td data-label={t('common.router')}>{c.router_name}</td>}
                  <td data-label={t('clients.status')}>
                    <StatusDot
                      tone={c.is_active ? 'ok' : 'off'}
                      label={c.is_active ? t('status.connected') : t('status.disconnected')}
                      showLabel
                    />
                  </td>
                  <td data-label={t('clients.currentTraffic')}>
                    {formatBps(c.current_tx_bps)} ↓ / {formatBps(c.current_rx_bps)} ↑
                  </td>
                  <td data-label={t('clients.accumulatedDownload')}>{formatBytes(c.accumulated_tx_bytes)}</td>
                  <td data-label={t('clients.accumulatedUpload')}>{formatBytes(c.accumulated_rx_bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && data.items.length === 0 && <p className="empty">{t('clients.noMatch')}</p>}

        <div className="controls" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="secondary"
            disabled={page <= 1}
            onClick={() => update({ page: String(page - 1) })}
          >
            ‹ {t('clients.previous')}
          </button>
          <span className="muted">
            {t('clients.page', { page: Math.min(page, pageCount), count: pageCount })}
          </span>
          <button
            type="button"
            className="secondary"
            disabled={page >= pageCount}
            onClick={() => update({ page: String(page + 1) })}
          >
            {t('clients.next')} ›
          </button>
          <span className="muted">
            {t('clients.showing', { first: formatCount(first), last: formatCount(last), total: formatCount(total) })}
          </span>
          <label className="checkbox">
            {t('clients.perPage')}
            <select value={size} onChange={(e) => update({ size: e.target.value })}>
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        </div>
    </Panel>
  )
}
