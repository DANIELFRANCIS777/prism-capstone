export default function UsageCard({ usage }) {
  if (!usage) return null
  return (
    <div className="card">
      <h3>Usage</h3>
      <dl>
        <dt>Requests</dt>
        <dd>{usage.requests}</dd>
        <dt>Prompt tokens</dt>
        <dd>{usage.prompt_tokens}</dd>
        <dt>Completion tokens</dt>
        <dd>{usage.completion_tokens}</dd>
        <dt>Cost (USD)</dt>
        <dd>${Number(usage.cost_usd).toFixed(6)}</dd>
        <dt>Cache hits</dt>
        <dd>{usage.cache_hits}</dd>
      </dl>
    </div>
  )
}
