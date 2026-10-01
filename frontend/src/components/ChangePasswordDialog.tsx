import { useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, apiFetch } from '../api/client'
import { useT } from '../i18n'
import { Dialog } from './Dialog'

export const MIN_PASSWORD_LENGTH = 10

export function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [saving, setSaving] = useState(false)
  const { t } = useT()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (next.length < MIN_PASSWORD_LENGTH) {
      setError(t('password.tooShort', { n: MIN_PASSWORD_LENGTH }))
      return
    }
    if (next !== repeat) {
      setError(t('password.mismatch'))
      return
    }
    setSaving(true)
    try {
      await apiFetch('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: current, new_password: next }),
      })
      setDone(true)
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError(t('login.tooMany'))
      else if (err instanceof ApiError && err.status === 400) setError(t('password.wrongCurrent'))
      else setError(t('password.changeError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog title={t('nav.changePassword')} onClose={onClose}>
      {done ? (
        <>
          <p className="success">{t('password.changed')}</p>
          <div className="actions">
            <button type="button" onClick={onClose}>
              {t('common.close')}
            </button>
          </div>
        </>
      ) : (
        <form onSubmit={handleSubmit}>
          <label>
            {t('password.current')}
            <input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
              autoFocus
            />
          </label>
          <label>
            {t('password.new', { n: MIN_PASSWORD_LENGTH })}
            <input
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
            />
          </label>
          <label>
            {t('password.repeatNew')}
            <input
              type="password"
              autoComplete="new-password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              required
            />
          </label>
          {error && <p className="error">{error}</p>}
          <div className="actions">
            <button type="submit" disabled={saving}>
              {t('password.change')}
            </button>
            <button type="button" className="secondary" onClick={onClose}>
              {t('common.cancel')}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}
