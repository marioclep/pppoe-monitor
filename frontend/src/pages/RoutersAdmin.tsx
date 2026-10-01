import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { StatusDot } from '../components/StatusDot'
import { useAuth } from '../context/AuthContext'
import { useT } from '../i18n'
import { t as translate } from '../i18n/core'
import { formatSince } from '../utils/format'
import { ROUTER_STATUS_KEY, routerStatus } from '../utils/routerStatus'

interface RouterRow {
  id: number
  name: string
  host: string
  port: number
  api_username: string
  use_tls: boolean
  verify_tls: boolean
  enabled: boolean
  last_polled_at: string | null
}

// One form serves both "add" (id null) and "edit". The port is kept as the
// raw input text so the field can be cleared while typing; it is parsed and
// validated only on save/test.
interface RouterFormState {
  id: number | null
  name: string
  host: string
  port: string
  api_username: string
  api_password: string
  use_tls: boolean
  verify_tls: boolean
  enabled: boolean
}

interface ConnectionTestResult {
  ok: boolean
  message: string
  routeros_version: string | null
  board_name: string | null
  active_sessions: number | null
}

// null = not tested (or form changed since), 'pending' = probe in flight.
type TestState = ConnectionTestResult | 'pending' | null

const DEFAULT_PORT = { tls: '443', plain: '80' }

const NEW_ROUTER: RouterFormState = {
  id: null,
  name: '',
  host: '',
  port: DEFAULT_PORT.tls,
  api_username: '',
  api_password: '',
  use_tls: true,
  verify_tls: false,
  enabled: true,
}

function toFormState(r: RouterRow): RouterFormState {
  return {
    id: r.id,
    name: r.name,
    host: r.host,
    port: String(r.port),
    api_username: r.api_username,
    // The API never returns the stored password; blank = keep it.
    api_password: '',
    use_tls: r.use_tls,
    verify_tls: r.verify_tls,
    enabled: r.enabled,
  }
}

function parsePort(raw: string): number | null {
  if (!/^\d+$/.test(raw.trim())) return null
  const port = Number(raw)
  return port >= 1 && port <= 65535 ? port : null
}

async function runConnectionTest(path: string, body?: object): Promise<ConnectionTestResult> {
  try {
    return await apiFetch<ConnectionTestResult>(path, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    return {
      ok: false,
      message: translate('routers.testFailed'),
      routeros_version: null,
      board_name: null,
      active_sessions: null,
    }
  }
}

function TestResult({ state }: { state: TestState }) {
  const { t } = useT()
  if (state === null) return null
  if (state === 'pending') return <p>{t('routers.testing')}</p>
  return (
    <p className={state.ok ? 'success' : 'error'}>
      {state.ok ? '✓ ' : '✗ '}
      {state.message}
    </p>
  )
}

interface RouterFormProps {
  initial: RouterFormState
  onSaved: () => void
  onCancel: () => void
}

function RouterForm({ initial, onSaved, onCancel }: RouterFormProps) {
  const [form, setForm] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const [test, setTest] = useState<TestState>(null)
  const ref = useRef<HTMLFormElement>(null)
  const isEdit = form.id !== null
  const { t } = useT()

  useEffect(() => {
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  // Any change invalidates the previous test result.
  function update(changes: Partial<RouterFormState>) {
    setForm({ ...form, ...changes })
    setTest(null)
    setError(null)
  }

  function toggleTls(useTls: boolean) {
    // Follow the TLS switch with the matching default port, unless the admin
    // already typed a custom one.
    const isDefaultPort = form.port === DEFAULT_PORT.tls || form.port === DEFAULT_PORT.plain
    update({
      use_tls: useTls,
      port: isDefaultPort ? (useTls ? DEFAULT_PORT.tls : DEFAULT_PORT.plain) : form.port,
    })
  }

  // The fields both save and test send; null when the port is invalid.
  function connectionFields() {
    const port = parsePort(form.port)
    if (port === null) {
      setError(t('routers.invalidPort'))
      return null
    }
    const { host, api_username, use_tls, verify_tls, api_password } = form
    // Blank password while editing = keep (or probe with) the stored one.
    return { host, port, api_username, use_tls, verify_tls, ...(api_password ? { api_password } : {}) }
  }

  async function handleTest() {
    const fields = connectionFields()
    if (!fields) return
    setTest('pending')
    setTest(await runConnectionTest('/routers/test', { ...fields, router_id: form.id }))
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const fields = connectionFields()
    if (!fields) return
    const payload = { ...fields, name: form.name, enabled: form.enabled }
    try {
      if (isEdit) {
        await apiFetch(`/routers/${form.id}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await apiFetch('/routers', { method: 'POST', body: JSON.stringify(payload) })
      }
      onSaved()
    } catch {
      setError(isEdit ? t('routers.saveError') : t('routers.createError'))
    }
  }

  return (
    <form ref={ref} onSubmit={handleSubmit}>
      <h2>{isEdit ? t('routers.editTitle', { name: initial.name }) : t('routers.addTitle')}</h2>
      <label>
        {t('routers.name')}
        <input value={form.name} onChange={(e) => update({ name: e.target.value })} required />
      </label>
      <label>
        {t('routers.host')}
        <input value={form.host} onChange={(e) => update({ host: e.target.value })} required />
      </label>
      <label>
        {t('routers.port')}
        <input
          inputMode="numeric"
          value={form.port}
          onChange={(e) => update({ port: e.target.value.replace(/\D/g, '') })}
          required
        />
      </label>
      <label>
        {t('routers.apiUser')}
        <input value={form.api_username} onChange={(e) => update({ api_username: e.target.value })} required />
      </label>
      <label>
        {isEdit ? t('routers.apiPasswordKeep') : t('routers.apiPassword')}
        <input
          type="password"
          autoComplete="new-password"
          placeholder={isEdit ? '••••••••' : undefined}
          value={form.api_password}
          onChange={(e) => update({ api_password: e.target.value })}
          required={!isEdit}
        />
      </label>
      <label className="checkbox">
        <input type="checkbox" checked={form.use_tls} onChange={(e) => toggleTls(e.target.checked)} />
        {t('routers.useTls')}
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={form.verify_tls}
          disabled={!form.use_tls}
          onChange={(e) => update({ verify_tls: e.target.checked })}
        />
        {t('routers.verifyTls')}
      </label>
      {isEdit && (
        <label className="checkbox">
          <input type="checkbox" checked={form.enabled} onChange={(e) => update({ enabled: e.target.checked })} />
          {t('routers.enabledHelp')}
        </label>
      )}
      {error && <p className="error">{error}</p>}
      <TestResult state={test} />
      <div className="actions">
        <button type="submit">{isEdit ? t('routers.saveChanges') : t('routers.add')}</button>
        <button
          type="button"
          className="secondary"
          disabled={test === 'pending' || !form.host || !form.api_username}
          onClick={handleTest}
        >
          {t('routers.testConnection')}
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          {t('common.cancel')}
        </button>
      </div>
    </form>
  )
}

export function RoutersAdmin() {
  const navigate = useNavigate()
  const { canEdit } = useAuth()
  const { t } = useT()
  const [routers, setRouters] = useState<RouterRow[]>([])
  // The single open form (add or edit), or null. Only one is ever shown.
  const [open, setOpen] = useState<RouterFormState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rowTests, setRowTests] = useState<Record<number, TestState>>({})
  const [pollSeconds, setPollSeconds] = useState(300)
  useEffect(() => {
    apiFetch<{ polling_interval_seconds: number }>('/dashboard/summary')
      .then((s) => setPollSeconds(s.polling_interval_seconds))
      .catch(() => {
        /* keep the default for the status dot */
      })
  }, [])

  function reload() {
    apiFetch<RouterRow[]>('/routers')
      .then(setRouters)
      .catch(() => setError(t('routers.loadError')))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps -- load once; t only changes error texts
  useEffect(reload, [])

  async function testRow(routerRow: RouterRow) {
    setRowTests((prev) => ({ ...prev, [routerRow.id]: 'pending' }))
    const result = await runConnectionTest(`/routers/${routerRow.id}/test`)
    setRowTests((prev) => ({ ...prev, [routerRow.id]: result }))
  }

  async function toggleEnabled(routerRow: RouterRow) {
    setError(null)
    try {
      await apiFetch(`/routers/${routerRow.id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !routerRow.enabled }),
      })
      reload()
    } catch {
      setError(t('routers.updateError'))
    }
  }

  async function handleDelete(routerRow: RouterRow) {
    const confirmed = confirm(t('routers.deleteConfirm', { name: routerRow.name }))
    if (!confirmed) return
    setError(null)
    try {
      await apiFetch(`/routers/${routerRow.id}`, { method: 'DELETE' })
      if (open?.id === routerRow.id) setOpen(null)
      reload()
    } catch {
      setError(t('routers.deleteError'))
    }
  }

  return (
    <>
      <PageHeader title={t('nav.routers')}>
        {canEdit && open === null && (
          <button type="button" onClick={() => setOpen(NEW_ROUTER)}>
            + {t('routers.addButton')}
          </button>
        )}
      </PageHeader>

      {open && (
        <Panel>
          <RouterForm
            // Remount on switching routers so the form starts from fresh state.
            key={open.id ?? 'new'}
            initial={open}
            onSaved={() => {
              setOpen(null)
              reload()
            }}
            onCancel={() => setOpen(null)}
          />
        </Panel>
      )}

      {error && <p className="error">{error}</p>}

      <Panel>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('routers.name')}</th>
                <th>Host</th>
                <th>{t('routers.apiUser')}</th>
                <th>{t('common.lastPoll')}</th>
                <th>{t('routers.enabled')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {routers.map((r) => {
                const status = routerStatus(r.last_polled_at, pollSeconds)
                return (
                  <tr key={r.id}>
                    <td data-label={t('routers.name')}>
                      <StatusDot
                        tone={r.enabled ? status : 'off'}
                        label={r.enabled ? t(ROUTER_STATUS_KEY[status]) : t('status.disabled')}
                      />{' '}
                      <Link to={`/routers/${r.id}`}>{r.name}</Link>
                    </td>
                    <td data-label="Host" className="num">
                      {r.host}:{r.port}
                    </td>
                    <td data-label={t('routers.apiUser')}>{r.api_username}</td>
                    <td data-label={t('common.lastPoll')}>{formatSince(r.last_polled_at)}</td>
                    <td data-label={t('routers.enabled')}>
                      <input
                        type="checkbox"
                        checked={r.enabled}
                        disabled={!canEdit}
                        onChange={() => toggleEnabled(r)}
                      />
                    </td>
                    <td data-label={t('common.actions')}>
                      <div className="actions">
                        <button type="button" className="secondary" onClick={() => navigate(`/routers/${r.id}`)}>
                          {t('common.view')}
                        </button>
                        {canEdit && (
                          <>
                            <button type="button" className="secondary" onClick={() => setOpen(toFormState(r))}>
                              {t('common.edit')}
                            </button>
                            <button
                              type="button"
                              className="secondary"
                              disabled={rowTests[r.id] === 'pending'}
                              onClick={() => testRow(r)}
                            >
                              {t('routers.test')}
                            </button>
                            <button type="button" className="danger" onClick={() => handleDelete(r)}>
                              {t('common.delete')}
                            </button>
                          </>
                        )}
                      </div>
                      <TestResult state={rowTests[r.id] ?? null} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {routers.length === 0 && <p className="empty">{t('routers.none')}</p>}
      </Panel>
    </>
  )
}
