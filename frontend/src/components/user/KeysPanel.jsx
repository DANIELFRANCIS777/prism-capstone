import { useEffect, useState } from 'react'
import { useUserAuth } from '../../UserAuthContext'
import { publicApi, userApi } from '../../api'

export default function KeysPanel({ onKeysChanged }) {
  const { authFetch } = useUserAuth()
  const [keys, setKeys] = useState([])
  const [authConfig, setAuthConfig] = useState(null)
  const [requestsPerMinute, setRequestsPerMinute] = useState(10)
  const [monthlyBudgetUsd, setMonthlyBudgetUsd] = useState(5)
  const [label, setLabel] = useState('')
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  // Shown exactly once, right after creation - the backend never returns
  // the raw secret again after this response.
  const [justCreatedKey, setJustCreatedKey] = useState(null)

  const load = () =>
    authFetch((token) => userApi.listKeys(token))
      .then((result) => {
        setKeys(result)
        onKeysChanged?.(result)
      })
      .catch((err) => setError(err.message))

  useEffect(() => {
    load()
    publicApi.authConfig().then(setAuthConfig).catch(() => setAuthConfig(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleCreate(e) {
    e.preventDefault()
    setError(null)
    setCreating(true)
    setJustCreatedKey(null)
    try {
      const created = await authFetch((token) =>
        userApi.createKey(token, {
          label: label || undefined,
          requestsPerMinute: Number(requestsPerMinute),
          monthlyBudgetUsd: Number(monthlyBudgetUsd),
        }),
      )
      setJustCreatedKey(created.virtual_key)
      setLabel('')
      await load()
    } catch (err) {
      setError(err.message || 'Failed to create key')
    } finally {
      setCreating(false)
    }
  }

  async function handleDisable(keyId) {
    setError(null)
    try {
      await authFetch((token) => userApi.setKeyStatus(token, keyId, 'disabled'))
      await load()
    } catch (err) {
      setError(err.message || 'Failed to update key')
    }
  }

  const atCap = authConfig && keys.length >= (authConfig.max_keys_per_org ?? 1)

  return (
    <section className="panel">
      <h2>API keys</h2>

      {justCreatedKey && (
        <div className="key-reveal">
          <p>
            Your new key (copy it now — it won't be shown again):
            <br />
            <code>{justCreatedKey}</code>
          </p>
        </div>
      )}

      {keys.length > 0 && (
        <ul className="keys-list">
          {keys.map((k) => (
            <li key={k.id}>
              <span className="credential-provider">{k.label || 'self-serve'}</span>
              <span className="credential-prefix">{k.key_prefix}…</span>
              <span>{k.requests_per_minute} req/min</span>
              <span>${Number(k.monthly_budget_usd).toFixed(2)}/mo</span>
              <span className={`badge status-${k.status === 'active' ? 'ok' : k.status}`}>
                {k.status}
              </span>
              {k.status === 'active' && (
                <button className="secondary" onClick={() => handleDisable(k.id)}>
                  Disable
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {!atCap ? (
        <form className="inline-form" onSubmit={handleCreate}>
          <label>
            Label (optional)
            <input value={label} onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label>
            Requests / minute
            <input
              type="number"
              min={1}
              max={authConfig?.max_requests_per_minute ?? undefined}
              required
              value={requestsPerMinute}
              onChange={(e) => setRequestsPerMinute(e.target.value)}
            />
          </label>
          <label>
            Monthly budget (USD)
            <input
              type="number"
              min={0}
              step="0.01"
              max={authConfig?.max_monthly_budget_usd ?? undefined}
              required
              value={monthlyBudgetUsd}
              onChange={(e) => setMonthlyBudgetUsd(e.target.value)}
            />
          </label>
          <button type="submit" disabled={creating}>
            {creating ? 'Creating…' : 'Create key'}
          </button>
        </form>
      ) : (
        <p className="hint">
          This account already has the maximum number of keys ({authConfig.max_keys_per_org}).
        </p>
      )}

      {error && <p className="error">{error}</p>}
    </section>
  )
}
