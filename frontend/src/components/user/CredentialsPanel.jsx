import { useEffect, useState } from 'react'
import { useUserAuth } from '../../UserAuthContext'
import { userApi } from '../../api'

// Matches config/gateway_config.json's provider names for this deployment.
// There's no public endpoint exposing configured provider names yet - if
// providers become operator-editable (ROADMAP.md Phase 3), this should read
// from that instead of being hardcoded here.
const KNOWN_PROVIDERS = ['alpha', 'beta', 'groq', 'gemini']

export default function CredentialsPanel() {
  const { authFetch } = useUserAuth()
  const [credentials, setCredentials] = useState([])
  const [provider, setProvider] = useState(KNOWN_PROVIDERS[0])
  const [apiKey, setApiKey] = useState('')
  const [label, setLabel] = useState('')
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  const load = () =>
    authFetch((token) => userApi.listCredentials(token))
      .then(setCredentials)
      .catch((err) => setError(err.message))

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      await authFetch((token) => userApi.putCredential(token, provider, apiKey, label || undefined))
      setApiKey('')
      setLabel('')
      await load()
    } catch (err) {
      setError(err.message || 'Failed to save credential')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(p) {
    setError(null)
    try {
      await authFetch((token) => userApi.deleteCredential(token, p))
      await load()
    } catch (err) {
      setError(err.message || 'Failed to remove credential')
    }
  }

  return (
    <section className="panel">
      <h2>Provider credentials (BYOK)</h2>
      <p className="hint">
        Add your own upstream provider API key so Prism dispatches your traffic through it
        instead of the platform's shared key. Without one, requests still work using the
        platform's own provider config.
      </p>

      {credentials.length > 0 && (
        <ul className="credentials-list">
          {credentials.map((c) => (
            <li key={c.provider}>
              <span className="credential-provider">{c.provider}</span>
              <span className="credential-prefix">{c.key_prefix}</span>
              {c.label && <span className="credential-label">{c.label}</span>}
              <button className="secondary" onClick={() => handleDelete(c.provider)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <form className="inline-form" onSubmit={handleSubmit}>
        <label>
          Provider
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            {KNOWN_PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label>
          API key
          <input
            type="password"
            required
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-…"
          />
        </label>
        <label>
          Label (optional)
          <input value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <button type="submit" disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </form>

      {error && <p className="error">{error}</p>}
    </section>
  )
}
