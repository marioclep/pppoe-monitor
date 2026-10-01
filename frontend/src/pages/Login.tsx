import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { Footer } from '../components/Footer'
import { ThemeToggle } from '../components/ThemeToggle'
import { useAuth } from '../context/AuthContext'
import { useT } from '../i18n'

export function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const { login } = useAuth()
  const { t } = useT()
  const navigate = useNavigate()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await login(username, password)
      navigate('/')
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? t('login.tooMany')
          : t('login.invalid'),
      )
    }
  }

  return (
    <div className="login-page">
      <div className="corner">
        <ThemeToggle />
      </div>
      <section className="panel login-card">
        <form onSubmit={handleSubmit}>
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            {t('app.name')}
          </div>
          <label>
            {t('common.user')}
            <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label>
            {t('common.password')}
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit">{t('login.submit')}</button>
        </form>
      </section>
      <Footer />
    </div>
  )
}
