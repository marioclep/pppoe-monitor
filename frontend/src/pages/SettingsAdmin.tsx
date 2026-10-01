import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { AlertThresholds } from '../components/AlertThresholds'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { useAuth } from '../context/AuthContext'
import { useSite } from '../context/SiteContext'
import { useT } from '../i18n'
import type { Lang } from '../i18n'
import { CHANNEL_LABEL, DIRECTION_KEY, formatGb } from '../utils/alerts'
import type { Threshold } from '../utils/alerts'

interface SettingsData {
  polling_interval_seconds: number
  reset_day_of_month: number
  retention_days: number
  raw_retention_days: number
  smtp_host: string | null
  smtp_port: number | null
  smtp_username: string | null
  smtp_password: string | null
  smtp_from: string | null
  smtp_to: string | null
  telegram_bot_token: string | null
  telegram_chat_id: string | null
  smtp_password_set: boolean
  telegram_bot_token_set: boolean
  site_name: string | null
  language: Lang
}


export function SettingsAdmin() {
  const { canEdit } = useAuth()
  const { setSiteName } = useSite()
  const { t, setLang } = useT()
  const [settings, setSettings] = useState<SettingsData | null>(null)
  const [smtpPasswordInput, setSmtpPasswordInput] = useState('')
  const [telegramTokenInput, setTelegramTokenInput] = useState('')
  const [thresholds, setThresholds] = useState<Threshold[]>([])
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [settingsSuccess, setSettingsSuccess] = useState(false)
  const [thresholdError, setThresholdError] = useState<string | null>(null)

  function reloadThresholds() {
    apiFetch<Threshold[]>('/alerts/thresholds')
      .then((all) => setThresholds(all.filter((th) => th.client_id !== null)))
      .catch(() => setThresholdError(t('thresholds.loadError')))
  }

  function reload() {
    reloadThresholds()
    apiFetch<SettingsData>('/settings')
      .then((data) => {
        setSettings(data)
        setSmtpPasswordInput('')
        setTelegramTokenInput('')
      })
      .catch(() => setSettingsError(t('settings.loadError')))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps -- load once; t only changes error texts
  useEffect(reload, [])

  async function saveSettings(e: FormEvent) {
    e.preventDefault()
    if (!settings) return
    setSettingsError(null)
    setSettingsSuccess(false)
    if (settings.raw_retention_days > settings.retention_days) {
      setSettingsError(t('settings.rawRetentionTooLong'))
      return
    }
    const payload: Record<string, unknown> = {
      polling_interval_seconds: settings.polling_interval_seconds,
      reset_day_of_month: settings.reset_day_of_month,
      retention_days: settings.retention_days,
      raw_retention_days: settings.raw_retention_days,
      smtp_host: settings.smtp_host,
      smtp_port: settings.smtp_port,
      smtp_username: settings.smtp_username,
      smtp_from: settings.smtp_from,
      smtp_to: settings.smtp_to,
      telegram_chat_id: settings.telegram_chat_id,
      site_name: settings.site_name,
      language: settings.language,
    }
    if (smtpPasswordInput) payload.smtp_password = smtpPasswordInput
    if (telegramTokenInput) payload.telegram_bot_token = telegramTokenInput

    try {
      const saved = await apiFetch<SettingsData>('/settings', { method: 'PUT', body: JSON.stringify(payload) })
      setSiteName(saved.site_name)
      setLang(saved.language)
      setSettingsSuccess(true)
      reload()
    } catch {
      setSettingsError(t('settings.saveError'))
    }
  }

  async function deleteThreshold(id: number) {
    setThresholdError(null)
    try {
      await apiFetch(`/alerts/thresholds/${id}`, { method: 'DELETE' })
      reloadThresholds()
    } catch {
      setThresholdError(t('thresholds.deleteError'))
    }
  }

  if (!settings) return <p>{t('common.loading')}</p>

  return (
    <>
      <PageHeader title={t('nav.settings')} />
      {!canEdit && <p className="muted">{t('settings.readonlyNote')}</p>}

      <Panel>
        <form onSubmit={saveSettings}>
          <fieldset className="plain" disabled={!canEdit}>
            <h2>{t('settings.general')}</h2>
            <label>
              {t('settings.siteName')}
              <input
                maxLength={40}
                placeholder={t('settings.siteNamePlaceholder')}
                value={settings.site_name ?? ''}
                onChange={(e) => setSettings({ ...settings, site_name: e.target.value || null })}
              />
              <small>{t('settings.siteNameHelp')}</small>
            </label>
            <label>
              Idioma / Language
              <select
                value={settings.language}
                onChange={(e) => setSettings({ ...settings, language: e.target.value as Lang })}
              >
                <option value="es">Español</option>
                <option value="en">English</option>
              </select>
              <small>{t('settings.languageHelp')}</small>
            </label>
            <label>
              {t('settings.pollingInterval')}
              <input
                type="number"
                min={60}
                value={settings.polling_interval_seconds}
                onChange={(e) => setSettings({ ...settings, polling_interval_seconds: Number(e.target.value) })}
              />
            </label>
            <label>
              {t('settings.resetDay')}
              <input
                type="number"
                min={1}
                max={28}
                value={settings.reset_day_of_month}
                onChange={(e) => setSettings({ ...settings, reset_day_of_month: Number(e.target.value) })}
              />
            </label>
            <label>
              {t('settings.retention')}
              <input
                type="number"
                min={1}
                value={settings.retention_days}
                onChange={(e) => setSettings({ ...settings, retention_days: Number(e.target.value) })}
              />
              <small>{t('settings.retentionHelp')}</small>
            </label>
            <label>
              {t('settings.rawRetention')}
              <input
                type="number"
                min={1}
                max={settings.retention_days}
                value={settings.raw_retention_days}
                onChange={(e) => setSettings({ ...settings, raw_retention_days: Number(e.target.value) })}
              />
              <small>{t('settings.rawRetentionHelp')}</small>
            </label>

            <h2>SMTP</h2>
            <label>
              Host
              <input
                value={settings.smtp_host ?? ''}
                onChange={(e) => setSettings({ ...settings, smtp_host: e.target.value || null })}
              />
            </label>
            <label>
              {t('routers.port')}
              <input
                type="number"
                value={settings.smtp_port ?? ''}
                onChange={(e) => setSettings({ ...settings, smtp_port: e.target.value ? Number(e.target.value) : null })}
              />
            </label>
            <label>
              {t('common.user')}
              <input
                value={settings.smtp_username ?? ''}
                onChange={(e) => setSettings({ ...settings, smtp_username: e.target.value || null })}
              />
            </label>
            <label>
              {t('common.password')}{' '}
              {settings.smtp_password_set ? t('settings.secretSet') : t('settings.secretUnset')}
              <input
                type="password"
                placeholder={settings.smtp_password_set ? '********' : ''}
                value={smtpPasswordInput}
                onChange={(e) => setSmtpPasswordInput(e.target.value)}
              />
            </label>
            <label>
              {t('settings.smtpFrom')}
              <input
                value={settings.smtp_from ?? ''}
                onChange={(e) => setSettings({ ...settings, smtp_from: e.target.value || null })}
              />
            </label>
            <label>
              {t('settings.smtpTo')}
              <input
                value={settings.smtp_to ?? ''}
                onChange={(e) => setSettings({ ...settings, smtp_to: e.target.value || null })}
              />
            </label>

            <h2>Telegram</h2>
            <label>
              {t('settings.botToken')}{' '}
              {settings.telegram_bot_token_set ? t('settings.secretSet') : t('settings.secretUnset')}
              <input
                type="password"
                placeholder={settings.telegram_bot_token_set ? '********' : ''}
                value={telegramTokenInput}
                onChange={(e) => setTelegramTokenInput(e.target.value)}
              />
            </label>
            <label>
              Chat ID
              <input
                value={settings.telegram_chat_id ?? ''}
                onChange={(e) => setSettings({ ...settings, telegram_chat_id: e.target.value || null })}
              />
            </label>
          </fieldset>
          {settingsError && <p className="error">{settingsError}</p>}
          {settingsSuccess && <p className="success">{t('settings.saved')}</p>}
          {canEdit && <button type="submit">{t('common.save')}</button>}
        </form>
      </Panel>

      <Panel title={t('thresholds.globalTitle')}>
        <p className="muted">{t('thresholds.globalHelp')}</p>
        <AlertThresholds clientId={null} />
      </Panel>

      <Panel title={t('thresholds.perClientTitle')}>
        <p className="muted">{t('thresholds.perClientHelp')}</p>
        {thresholdError && <p className="error">{thresholdError}</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('common.client')}</th>
                <th>{t('common.router')}</th>
                <th>{t('thresholds.direction')}</th>
                <th>{t('thresholds.threshold')}</th>
                <th>{t('thresholds.channel')}</th>
                {canEdit && <th></th>}
              </tr>
            </thead>
            <tbody>
              {thresholds.map((th) => (
                <tr key={th.id}>
                  <td data-label={t('common.client')}>
                    <Link to={`/clients/${th.client_id}`}>{th.client_username}</Link>
                  </td>
                  <td data-label={t('common.router')}>{th.router_name}</td>
                  <td data-label={t('thresholds.direction')}>{t(DIRECTION_KEY[th.direction])}</td>
                  <td data-label={t('thresholds.threshold')} className="num">
                    {formatGb(th.bytes_threshold)}
                  </td>
                  <td data-label={t('thresholds.channel')}>{CHANNEL_LABEL[th.notify_channel] ?? th.notify_channel}</td>
                  {canEdit && (
                    <td data-label={t('common.actions')}>
                      <button className="danger" onClick={() => deleteThreshold(th.id)}>
                        {t('common.delete')}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {thresholds.length === 0 && <p className="empty">{t('thresholds.noneForClients')}</p>}
      </Panel>
    </>
  )
}
