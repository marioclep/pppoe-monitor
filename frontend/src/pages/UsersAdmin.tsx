import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, apiFetch } from '../api/client'
import { MIN_PASSWORD_LENGTH } from '../components/ChangePasswordDialog'
import { Dialog } from '../components/Dialog'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { useAuth } from '../context/AuthContext'
import type { Role } from '../context/AuthContext'
import { useT } from '../i18n'
import type { MessageKey } from '../i18n'
import { formatDate } from '../utils/format'

interface UserRow {
  id: number
  username: string
  role: Role
  created_at: string
}

type Translate = ReturnType<typeof useT>['t']

const ROLE_KEY: Record<Role, MessageKey> = { full: 'role.full', readonly: 'role.readonly' }

/** Password + repeat; returns an error message, or null when fine. An empty
 *  password is fine only when it is optional (edit = keep the current one). */
function checkPassword(t: Translate, password: string, repeat: string, optional: boolean): string | null {
  if (optional && password === '' && repeat === '') return null
  if (password.length < MIN_PASSWORD_LENGTH) return t('password.tooShort', { n: MIN_PASSWORD_LENGTH })
  if (password !== repeat) return t('password.mismatch')
  return null
}

function RoleSelect({ value, onChange }: { value: Role; onChange: (role: Role) => void }) {
  const { t } = useT()
  return (
    <label>
      {t('users.role')}
      <select value={value} onChange={(e) => onChange(e.target.value as Role)}>
        <option value="full">{t('users.roleFullHelp')}</option>
        <option value="readonly">{t('users.roleReadonlyHelp')}</option>
      </select>
    </label>
  )
}

function PasswordFields(props: {
  label: string
  password: string
  repeat: string
  onPassword: (v: string) => void
  onRepeat: (v: string) => void
  required: boolean
}) {
  const { t } = useT()
  return (
    <>
      <label>
        {props.label}
        <input
          type="password"
          autoComplete="new-password"
          value={props.password}
          onChange={(e) => props.onPassword(e.target.value)}
          required={props.required}
        />
      </label>
      <label>
        {t('users.repeatPassword')}
        <input
          type="password"
          autoComplete="new-password"
          value={props.repeat}
          onChange={(e) => props.onRepeat(e.target.value)}
          required={props.required}
        />
      </label>
    </>
  )
}

function NewUserDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { t } = useT()
  const [username, setUsername] = useState('')
  const [role, setRole] = useState<Role>('readonly')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const problem = checkPassword(t, password, repeat, false)
    if (problem) {
      setError(problem)
      return
    }
    try {
      await apiFetch('/users', { method: 'POST', body: JSON.stringify({ username: username.trim(), password, role }) })
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? t('users.exists') : t('users.createError'))
    }
  }

  return (
    <Dialog title={t('users.newTitle')} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <label>
          {t('common.user')}
          <input value={username} onChange={(e) => setUsername(e.target.value)} maxLength={64} required autoFocus />
        </label>
        <RoleSelect value={role} onChange={setRole} />
        <PasswordFields
          label={t('users.passwordMin', { n: MIN_PASSWORD_LENGTH })}
          password={password}
          repeat={repeat}
          onPassword={setPassword}
          onRepeat={setRepeat}
          required
        />
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="submit">{t('users.create')}</button>
          <button type="button" className="secondary" onClick={onClose}>
            {t('common.cancel')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function EditUserDialog({ user, onClose, onSaved }: { user: UserRow; onClose: () => void; onSaved: () => void }) {
  const { t } = useT()
  const [role, setRole] = useState<Role>(user.role)
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const problem = checkPassword(t, password, repeat, true)
    if (problem) {
      setError(problem)
      return
    }
    const payload = { ...(role !== user.role ? { role } : {}), ...(password ? { password } : {}) }
    if (Object.keys(payload).length === 0) {
      onClose()
      return
    }
    try {
      await apiFetch(`/users/${user.id}`, { method: 'PUT', body: JSON.stringify(payload) })
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? t('users.lastFull') : t('users.saveError'))
    }
  }

  return (
    <Dialog title={t('users.editTitle', { name: user.username })} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <RoleSelect value={role} onChange={setRole} />
        <PasswordFields
          label={t('users.newPasswordOptional')}
          password={password}
          repeat={repeat}
          onPassword={setPassword}
          onRepeat={setRepeat}
          required={false}
        />
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="submit">{t('common.save')}</button>
          <button type="button" className="secondary" onClick={onClose}>
            {t('common.cancel')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

export function UsersAdmin() {
  const { user: me, canEdit } = useAuth()
  const { t } = useT()
  const [users, setUsers] = useState<UserRow[]>([])
  const [error, setError] = useState<string | null>(null)
  // null = no dialog; 'new' = create; a row = edit that user.
  const [dialog, setDialog] = useState<UserRow | 'new' | null>(null)

  function reload() {
    apiFetch<UserRow[]>('/users')
      .then(setUsers)
      .catch(() => setError(t('users.loadError')))
  }

  useEffect(() => {
    if (canEdit) reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only depends on canEdit
  }, [canEdit])

  async function handleDelete(row: UserRow) {
    if (!confirm(t('users.deleteConfirm', { name: row.username }))) return
    setError(null)
    try {
      await apiFetch(`/users/${row.id}`, { method: 'DELETE' })
      reload()
    } catch {
      setError(t('users.deleteError'))
    }
  }

  function saved() {
    setDialog(null)
    setError(null)
    reload()
  }

  if (me && !canEdit) {
    return (
      <>
        <PageHeader title={t('nav.users')} />
        <p className="muted">{t('users.fullOnly')}</p>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t('nav.users')}>
        <button type="button" onClick={() => setDialog('new')} disabled={!canEdit}>
          + {t('users.newButton')}
        </button>
      </PageHeader>

      {dialog === 'new' && <NewUserDialog onClose={() => setDialog(null)} onSaved={saved} />}
      {dialog !== null && dialog !== 'new' && (
        <EditUserDialog user={dialog} onClose={() => setDialog(null)} onSaved={saved} />
      )}

      {error && <p className="error">{error}</p>}

      <Panel>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('common.user')}</th>
                <th>{t('users.role')}</th>
                <th>{t('users.created')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const isMe = u.username === me?.username
                return (
                  <tr key={u.id}>
                    <td data-label={t('common.user')}>
                      {u.username}
                      {isMe && <span className="muted"> ({t('users.you')})</span>}
                    </td>
                    <td data-label={t('users.role')}>
                      <span className={u.role === 'readonly' ? 'role-badge readonly' : 'role-badge'}>
                        {t(ROLE_KEY[u.role])}
                      </span>
                    </td>
                    <td data-label={t('users.created')}>{formatDate(u.created_at)}</td>
                    <td data-label={t('common.actions')}>
                      <div className="actions">
                        <button type="button" className="secondary" onClick={() => setDialog(u)}>
                          {t('common.edit')}
                        </button>
                        {!isMe && (
                          <button type="button" className="danger" onClick={() => handleDelete(u)}>
                            {t('common.delete')}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  )
}
