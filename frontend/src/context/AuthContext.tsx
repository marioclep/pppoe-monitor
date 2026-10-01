import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { apiFetch, getToken, setToken } from '../api/client'

export type Role = 'full' | 'readonly'

export interface CurrentUser {
  username: string
  role: Role
}

interface AuthContextValue {
  isAuthenticated: boolean
  /** null while it loads after login or a page reload. */
  user: CurrentUser | null
  /** Full access. False while the user is still loading: buttons appear
   *  once the role is known, never the other way around. */
  canEdit: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(!!getToken())
  const [user, setUser] = useState<CurrentUser | null>(null)

  const loadMe = useCallback(() => {
    apiFetch<CurrentUser>('/auth/me')
      .then(setUser)
      .catch(() => {
        /* a 401 already bounces to /login */
      })
  }, [])

  useEffect(() => {
    if (isAuthenticated) loadMe()
  }, [isAuthenticated, loadMe])

  async function login(username: string, password: string) {
    const result = await apiFetch<{ access_token: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setToken(result.access_token)
    setUser(null)
    setIsAuthenticated(true)
  }

  function logout() {
    setToken(null)
    setUser(null)
    setIsAuthenticated(false)
  }

  const canEdit = user?.role === 'full'

  return (
    <AuthContext.Provider value={{ isAuthenticated, user, canEdit, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
