import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useT } from '../i18n'
import type { MessageKey } from '../i18n'
import { ChangePasswordDialog } from './ChangePasswordDialog'
import { Footer } from './Footer'
import { Icon } from './Icon'
import type { IconName } from './Icon'

const NAV: { to: string; label: MessageKey; icon: IconName; fullOnly?: boolean }[] = [
  { to: '/dashboard', label: 'nav.dashboard', icon: 'dashboard' },
  { to: '/clients', label: 'nav.clients', icon: 'clients' },
  { to: '/routers', label: 'nav.routers', icon: 'routers' },
  { to: '/alerts', label: 'nav.alerts', icon: 'bell' },
  { to: '/server', label: 'nav.server', icon: 'server' },
  { to: '/settings', label: 'nav.settings', icon: 'settings' },
  { to: '/users', label: 'nav.users', icon: 'users', fullOnly: true },
]

function Brand() {
  const { t } = useT()
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true" />
      {t('app.name')}
    </div>
  )
}

export function Layout() {
  const { logout, user, canEdit } = useAuth()
  const { t } = useT()
  const [menuOpen, setMenuOpen] = useState(false)
  const [changingPassword, setChangingPassword] = useState(false)
  const location = useLocation()

  // Close the mobile menu after navigating.
  useEffect(() => setMenuOpen(false), [location.pathname])

  return (
    <div className={menuOpen ? 'app menu-open' : 'app'}>
      <aside className="sidebar">
        <Brand />
        <nav>
          {NAV.filter((item) => !item.fullOnly || canEdit).map((item) => (
            <NavLink key={item.to} to={item.to} className="nav-link">
              <Icon name={item.icon} />
              {t(item.label)}
            </NavLink>
          ))}
        </nav>
        <div className="spacer" />
        {user && (
          <div className="user-box">
            <strong>{user.username}</strong>
            {user.role === 'readonly' && <span className="role-badge readonly">{t('role.readonly')}</span>}
          </div>
        )}
        <button type="button" className="nav-link ghost" onClick={() => setChangingPassword(true)}>
          <Icon name="key" />
          {t('nav.changePassword')}
        </button>
        <button type="button" className="nav-link ghost" onClick={logout}>
          <Icon name="logout" />
          {t('nav.logout')}
        </button>
      </aside>
      {changingPassword && <ChangePasswordDialog onClose={() => setChangingPassword(false)} />}
      <div className="scrim" onClick={() => setMenuOpen(false)} />
      <div className="content">
        <header className="topbar">
          <button type="button" className="icon-button" aria-label={t('nav.openMenu')} onClick={() => setMenuOpen(true)}>
            <Icon name="menu" />
          </button>
          <Brand />
        </header>
        <main className="page">
          <Outlet />
        </main>
        <Footer />
      </div>
    </div>
  )
}
