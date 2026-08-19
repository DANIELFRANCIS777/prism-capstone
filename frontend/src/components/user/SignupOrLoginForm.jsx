import { useEffect, useState } from 'react'
import { useUserAuth } from '../../UserAuthContext'
import { publicApi, userAuthApi } from '../../api'

export default function SignupOrLoginForm() {
  const { login } = useUserAuth()
  const [mode, setMode] = useState('signup') // 'signup' | 'login'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [orgName, setOrgName] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [authConfig, setAuthConfig] = useState(null)

  useEffect(() => {
    publicApi.authConfig().then(setAuthConfig).catch(() => setAuthConfig(null))
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      if (mode === 'signup') {
        await userAuthApi.signup(email, password, orgName || undefined)
        // signup already returns a token pair; log in locally with the same
        // credentials so the shared AuthProvider picks up the session the
        // same way it would for an existing user.
        await login(email, password)
      } else {
        await login(email, password)
      }
    } catch (err) {
      setError(err.message || `${mode === 'signup' ? 'Signup' : 'Login'} failed`)
    } finally {
      setLoading(false)
    }
  }

  const signupDisabled = authConfig !== null && authConfig.signup_enabled === false

  return (
    <div className="login-page">
      <form className="login-form" onSubmit={handleSubmit}>
        <h1>Prism</h1>
        <div className="mode-toggle">
          <button
            type="button"
            className={mode === 'signup' ? '' : 'secondary'}
            onClick={() => setMode('signup')}
          >
            Sign up
          </button>
          <button
            type="button"
            className={mode === 'login' ? '' : 'secondary'}
            onClick={() => setMode('login')}
          >
            Log in
          </button>
        </div>

        {mode === 'signup' && signupDisabled && (
          <p className="error">Signup is currently disabled on this instance.</p>
        )}

        <label>
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {mode === 'signup' && (
          <label>
            Organization name (optional)
            <input value={orgName} onChange={(e) => setOrgName(e.target.value)} />
          </label>
        )}
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={loading || (mode === 'signup' && signupDisabled)}>
          {loading ? 'Working…' : mode === 'signup' ? 'Create account' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
