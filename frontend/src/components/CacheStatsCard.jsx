export default function CacheStatsCard({ stats }) {
  if (!stats) return null
  return (
    <div className="card">
      <h3>Cache stats</h3>
      <dl>
        <dt>Hits</dt>
        <dd>{stats.hits}</dd>
        <dt>Misses</dt>
        <dd>{stats.misses}</dd>
        <dt>Hit rate</dt>
        <dd>{(stats.hit_rate * 100).toFixed(1)}%</dd>
      </dl>
    </div>
  )
}
