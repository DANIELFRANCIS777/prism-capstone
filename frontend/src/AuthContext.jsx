import { createContext, useCallback, useContext, useEffect, useState } from 'react'
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

  useEffect(() => {
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
    if (tokens?.refresh_token) {
      try {
        await authApi.logout(tokens.refresh_token)
      } catch {
        // Token may already be invalid/expired - fine, we're logging out anyway.
      }
    }
    setTokens(null)
  }, [tokens])

  // Wraps an admin API call: on a 401 (expired access token), transparently
  // refreshes once via the stored refresh token and retries - the rest of the
  // app never has to think about token expiry.
  const authFetch = useCallback(
    async (fn) => {
      if (!tokens) throw new Error('Not logged in')
      try {
        return await fn(tokens.access_token)
      } catch (err) {
        if (err instanceof ApiError && err.status === 401 && tokens.refresh_token) {
          const refreshed = await authApi.refresh(tokens.refresh_token)
          const newTokens = {
            access_token: refreshed.access_token,
            refresh_token: refreshed.refresh_token,
          }
          setTokens(newTokens)
          return await fn(newTokens.access_token)
        }
        throw err
      }
    },
    [tokens],
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
