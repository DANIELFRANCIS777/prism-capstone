export default function RequestLogsTable({ logs }) {
  if (!logs || logs.length === 0) return <p className="empty">No requests yet.</p>

  return (
    <div className="table-wrap">
      <table className="logs-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Requested</th>
            <th>Served by</th>
            <th>Status</th>
            <th>Cache</th>
            <th>Fallback</th>
            <th>Retries</th>
            <th>Cost (USD)</th>
            <th>Latency</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr key={log.request_id}>
              <td>{new Date(log.created_at).toLocaleTimeString()}</td>
              <td>{log.requested_model}</td>
              <td>
                {log.resolved_provider ? `${log.resolved_provider}/${log.resolved_model}` : '—'}
              </td>
              <td>
                <span className={`badge status-${log.status}`}>{log.status}</span>
              </td>
              <td>{log.cache}</td>
              <td>{log.fallback ? 'yes' : 'no'}</td>
              <td>{log.retries}</td>
              <td>${Number(log.cost_usd).toFixed(6)}</td>
              <td>{log.latency_ms}ms</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
