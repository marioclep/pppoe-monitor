const TOKEN_STORAGE_KEY = 'pppoe_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token)
  else localStorage.removeItem(TOKEN_STORAGE_KEY)
}

const LOGIN_PATH = '/auth/login'

export class ApiError extends Error {
  status: number
  /** The backend's "detail" when it is a plain message, else null. */
  detail: string | null

  constructor(status: number, body: string) {
    super(`API error ${status}: ${body}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = null
    try {
      const parsed = JSON.parse(body)
      if (typeof parsed?.detail === 'string') this.detail = parsed.detail
    } catch {
      /* not JSON */
    }
  }
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`/api${path}`, { ...options, headers })
  if (!response.ok) {
    // A 401 on the login call itself just means bad credentials — let the
    // Login page surface that. A 401 on any other call means the stored
    // token is no longer valid, so clear it and bounce to /login.
    if (response.status === 401 && path !== LOGIN_PATH) {
      setToken(null)
      window.location.assign('/login')
    }
    const detail = await response.text()
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
