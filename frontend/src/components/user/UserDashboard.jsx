import { useCallback, useEffect, useState } from 'react'
import { useUserAuth } from '../../UserAuthContext'
import { userApi } from '../../api'
import CacheStatsCard from '../CacheStatsCard'
import RequestLogsTable from '../RequestLogsTable'
import UsageCard from '../UsageCard'
import CredentialsPanel from './CredentialsPanel'
import KeysPanel from './KeysPanel'

export default function UserDashboard() {
  const { authFetch, logout } = useUserAuth()
  const [me, setMe] = useState(null)
  const [usage, setUsage] = useState(null)
  const [logs, setLogs] = useState([])
  const [cacheStats, setCacheStats] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [hasKeys, setHasKeys] = useState(false)

  useEffect(() => {
    authFetch((token) => userApi.me(token))
      .then(setMe)
      .catch((err) => setError(err.message))
  }, [authFetch])

  const loadUsage = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [usageResult, logsResult, cacheResult] = await Promise.all([
        authFetch((token) => userApi.usage(token)),
        authFetch((token) => userApi.logs(token, 25)),
        authFetch((token) => userApi.cacheStats(token)),
      ])
      setUsage(usageResult)
      setLogs(logsResult)
      setCacheStats(cacheResult)
    } catch (err) {
      setError(err.message || 'Failed to load usage')
    } finally {
      setLoading(false)
    }
  }, [authFetch])

  useEffect(() => {
    loadUsage()
  }, [loadUsage])

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Prism</h1>
        <div className="header-right">
          {me && (
            <span className="org-label">
              {me.org.name || 'Your org'} · {me.email}
            </span>
          )}
          <button className="secondary" onClick={logout}>
            Log out
          </button>
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      <section>
        <h2>Usage</h2>
        <div className="controls">
          <button onClick={loadUsage} disabled={loading || !hasKeys}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
        {!hasKeys && <p className="hint">Create an API key below to start sending traffic.</p>}
        <section className="cards">
          <UsageCard usage={usage} />
          <CacheStatsCard stats={cacheStats} />
        </section>
        <h3>Recent requests</h3>
        <RequestLogsTable logs={logs} />
      </section>

      <KeysPanel onKeysChanged={(keys) => setHasKeys(keys.length > 0)} />
      <CredentialsPanel />
    </div>
  )
}
