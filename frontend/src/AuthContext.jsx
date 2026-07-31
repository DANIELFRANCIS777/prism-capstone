import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { ApiError, authApi } from './api'

const AuthContext = createContext(null)
const STORAGE_KEY = 'prism_admin_tokens'

function loadStoredTokens() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function AuthProvider({ children }) {
  const [tokens, setTokens] = useState(loadStoredTokens)
  // Mirrors `tokens` for callbacks that need the latest value without being
  // recreated (and without capturing a stale one) on every token change.
  const tokensRef = useRef(tokens)
  // At most one /admin/auth/refresh call in flight at a time - concurrent 401s
  // (e.g. the dashboard's parallel usage/logs/cache-stats fetches) all await
  // this same promise instead of each spending the single-use refresh token
  // themselves, which would trip the backend's reuse/theft detection and
  // revoke the whole session out from under a legitimate concurrent request.
  const refreshPromiseRef = useRef(null)

  useEffect(() => {
    tokensRef.current = tokens
    if (tokens) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(tokens))
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }, [tokens])

  const login = useCallback(async (username, password) => {
    const result = await authApi.login(username, password)
    setTokens({ access_token: result.access_token, refresh_token: result.refresh_token })
  }, [])

  const logout = useCallback(async () => {
    const current = tokensRef.current
    if (current?.refresh_token) {
      try {
        await authApi.logout(current.refresh_token)
      } catch {
        // Token may already be invalid/expired - fine, we're logging out anyway.
      }
    }
    setTokens(null)
  }, [])

  const refreshTokens = useCallback(() => {
    if (!refreshPromiseRef.current) {
      refreshPromiseRef.current = authApi
        .refresh(tokensRef.current.refresh_token)
        .then((refreshed) => {
          const newTokens = {
            access_token: refreshed.access_token,
            refresh_token: refreshed.refresh_token,
          }
          setTokens(newTokens)
          tokensRef.current = newTokens
          return newTokens
        })
        .catch((err) => {
          // The refresh token itself is invalid/expired/revoked - there is no
          // recovering this session. Clear it so the UI drops back to the
          // login form instead of getting stuck "authenticated" with every
          // subsequent call failing the same way.
          setTokens(null)
          tokensRef.current = null
          throw err
        })
        .finally(() => {
          refreshPromiseRef.current = null
        })
    }
    return refreshPromiseRef.current
  }, [])

  // Wraps an admin API call: on a 401 (expired access token), transparently
  // refreshes once (deduped via refreshTokens above) and retries - the rest
  // of the app never has to think about token expiry.
  const authFetch = useCallback(
    async (fn) => {
      const current = tokensRef.current
      if (!current) throw new Error('Not logged in')
      try {
        return await fn(current.access_token)
      } catch (err) {
        if (err instanceof ApiError && err.status === 401 && current.refresh_token) {
          const newTokens = await refreshTokens()
          return await fn(newTokens.access_token)
        }
        throw err
      }
    },
    [refreshTokens],
  )

  return (
    <AuthContext.Provider value={{ isAuthenticated: !!tokens, login, logout, authFetch }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
