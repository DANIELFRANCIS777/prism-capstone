import { useCallback, useEffect, useState } from 'react'
import { adminApi } from '../api'
import { useAuth } from '../AuthContext'
import CacheStatsCard from './CacheStatsCard'
import RequestLogsTable from './RequestLogsTable'
import UsageCard from './UsageCard'

const DEMO_KEYS = [
  { label: 'search (prism-sk-search-1a2b3c)', value: 'prism-sk-search-1a2b3c' },
  { label: 'research (prism-sk-research-4d5e6f)', value: 'prism-sk-research-4d5e6f' },
  { label: 'free-tier (prism-sk-free-7g8h9i)', value: 'prism-sk-free-7g8h9i' },
  { label: 'budget-demo (prism-sk-budget-demo-0j1k2l)', value: 'prism-sk-budget-demo-0j1k2l' },
]

export default function Dashboard() {
  const { authFetch, logout } = useAuth()
  const [selectedKey, setSelectedKey] = useState(DEMO_KEYS[0].value)
  const [usage, setUsage] = useState(null)
  const [logs, setLogs] = useState([])
  const [cacheStats, setCacheStats] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [usageResult, logsResult, cacheResult] = await Promise.all([
        authFetch((token) => adminApi.usage(token, selectedKey)),
        authFetch((token) => adminApi.logs(token, selectedKey, 25)),
        authFetch((token) => adminApi.cacheStats(token, selectedKey)),
      ])
      setUsage(usageResult)
      setLogs(logsResult)
      setCacheStats(cacheResult)
    } catch (err) {
      setError(err.message || 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [authFetch, selectedKey])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Prism Ops Console</h1>
        <button className="secondary" onClick={logout}>
          Log out
        </button>
      </header>

      <div className="controls">
        <label>
          Key
          <select value={selectedKey} onChange={(e) => setSelectedKey(e.target.value)}>
            {DEMO_KEYS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <button onClick={loadAll} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      <section className="cards">
        <UsageCard usage={usage} />
        <CacheStatsCard stats={cacheStats} />
      </section>

      <section>
        <h2>Recent requests</h2>
        <RequestLogsTable logs={logs} />
      </section>
    </div>
  )
}
