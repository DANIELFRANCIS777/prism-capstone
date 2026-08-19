import { useCallback, useEffect, useState } from 'react'
import { useAdminAuth } from '../AdminAuthContext'
import { adminApi } from '../api'
import CacheStatsCard from './CacheStatsCard'
import RequestLogsTable from './RequestLogsTable'
import UsageCard from './UsageCard'

export default function Dashboard() {
  const { authFetch, logout } = useAdminAuth()
  const [keys, setKeys] = useState([])
  const [selectedKeyId, setSelectedKeyId] = useState(null)
  const [usage, setUsage] = useState(null)
  const [logs, setLogs] = useState([])
  const [cacheStats, setCacheStats] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    authFetch((token) => adminApi.listKeys(token))
      .then((result) => {
        setKeys(result)
        if (result.length > 0) setSelectedKeyId(result[0].id)
      })
      .catch((err) => setError(err.message || 'Failed to load keys'))
  }, [authFetch])

  const loadAll = useCallback(async () => {
    if (selectedKeyId == null) return
    setLoading(true)
    setError(null)
    try {
      const [usageResult, logsResult, cacheResult] = await Promise.all([
        authFetch((token) => adminApi.usage(token, selectedKeyId)),
        authFetch((token) => adminApi.logs(token, selectedKeyId, 25)),
        authFetch((token) => adminApi.cacheStats(token, selectedKeyId)),
      ])
      setUsage(usageResult)
      setLogs(logsResult)
      setCacheStats(cacheResult)
    } catch (err) {
      setError(err.message || 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [authFetch, selectedKeyId])

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
          <select
            value={selectedKeyId ?? ''}
            onChange={(e) => setSelectedKeyId(Number(e.target.value))}
          >
            {keys.map((k) => (
              <option key={k.id} value={k.id}>
                {k.team} ({k.key_prefix}…){k.org_id ? '' : ' — operator-provisioned'}
              </option>
            ))}
          </select>
        </label>
        <button onClick={loadAll} disabled={loading || selectedKeyId == null}>
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
