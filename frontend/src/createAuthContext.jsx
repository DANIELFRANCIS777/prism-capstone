import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'

// Extracted from what was originally AuthContext.jsx-only logic, now shared
// by the platform-operator console and the tenant console - both need the
// same refresh-dedup + retry-once-on-401 behavior, just against different
// token storage and a different auth API (admin username/password vs.
// tenant email/password).
export function createAuthContext({ storageKey, api }) {
  const AuthContext = createContext(null)

  function loadStoredTokens() {
    try {
      const raw = localStorage.getItem(storageKey)
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  }

  function AuthProvider({ children }) {
    const [tokens, setTokens] = useState(loadStoredTokens)
    // Assigned directly during render (not in a useEffect) so it's always
    // current by the time any effect runs - including a child component
    // that mounts fresh in the same commit where tokens transitions from
    // null to set (e.g. the dashboard mounting right after login) and calls
    // authFetch from its own mount effect. Effects fire child-before-parent
    // within a commit, so a ref synced via useEffect here would still be
    // stale when that child effect runs, and authFetch would wrongly throw
    // "Not logged in" on the very first render after a successful login.
    const tokensRef = useRef(tokens)
    tokensRef.current = tokens
    // At most one refresh call in flight at a time - concurrent 401s (e.g.
    // parallel dashboard fetches) all await this same promise instead of
    // each spending the single-use refresh token themselves, which would
    // trip the backend's reuse/theft detection and revoke the whole session
    // out from under a legitimate concurrent request.
    const refreshPromiseRef = useRef(null)

    useEffect(() => {
      if (tokens) {
        localStorage.setItem(storageKey, JSON.stringify(tokens))
      } else {
        localStorage.removeItem(storageKey)
      }
    }, [tokens])

    const login = useCallback(async (...args) => {
      const result = await api.login(...args)
      setTokens({ access_token: result.access_token, refresh_token: result.refresh_token })
    }, [])

    const logout = useCallback(async () => {
      const current = tokensRef.current
      if (current?.refresh_token) {
        try {
          await api.logout(current.refresh_token)
        } catch {
          // Token may already be invalid/expired - fine, we're logging out anyway.
        }
      }
      setTokens(null)
    }, [])

    const refreshTokens = useCallback(() => {
      if (!refreshPromiseRef.current) {
        refreshPromiseRef.current = api
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

  function useAuthContext() {
    const ctx = useContext(AuthContext)
    if (!ctx) throw new Error('useAuth must be used within its AuthProvider')
    return ctx
  }

  return { AuthProvider, useAuth: useAuthContext }
}
