const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080'

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function request(path, { method = 'GET', token, body } = {}) {
  const headers = {}
  // Only declare a content type for a body we're actually sending - a
  // Content-Type on a body-less GET describes nothing and some proxies
  // treat it as malformed.
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  // Read as text first, then parse only if there's something to parse: an
  // empty body served with a JSON content-type (a 204, or a proxy-generated
  // error page) would otherwise throw a SyntaxError that isn't an ApiError,
  // escaping the error handling below instead of surfacing through it.
  const contentType = res.headers.get('content-type') || ''
  const raw = await res.text()
  let data = raw
  if (raw && contentType.includes('application/json')) {
    try {
      data = JSON.parse(raw)
    } catch {
      data = raw // malformed JSON - fall back to the raw text for the message
    }
  }

  if (!res.ok) {
    const message = data?.error?.message || data?.detail || `Request failed (${res.status})`
    throw new ApiError(message, res.status, data)
  }
  return data
}

export const authApi = {
  login: (username, password) =>
    request('/admin/auth/login', { method: 'POST', body: { username, password } }),
  refresh: (refresh_token) =>
    request('/admin/auth/refresh', { method: 'POST', body: { refresh_token } }),
  logout: (refresh_token) =>
    request('/admin/auth/logout', { method: 'POST', body: { refresh_token } }),
}

export const adminApi = {
  listKeys: (token) => request('/admin/keys', { token }),
  usage: (token, keyId, from, to) => {
    const params = new URLSearchParams({ key_id: keyId })
    if (from) params.set('from', from)
    if (to) params.set('to', to)
    return request(`/admin/usage?${params}`, { token })
  },
  logs: (token, keyId, limit = 25) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (keyId) params.set('key_id', keyId)
    return request(`/admin/logs?${params}`, { token })
  },
  cacheStats: (token, keyId) => {
    const params = new URLSearchParams()
    if (keyId) params.set('key_id', keyId)
    return request(`/admin/cache/stats?${params}`, { token })
  },
}

export const publicApi = {
  authConfig: () => request('/auth/config'),
}

export const userAuthApi = {
  signup: (email, password, orgName) =>
    request('/auth/signup', { method: 'POST', body: { email, password, org_name: orgName } }),
  login: (email, password) => request('/auth/login', { method: 'POST', body: { email, password } }),
  refresh: (refresh_token) => request('/auth/refresh', { method: 'POST', body: { refresh_token } }),
  logout: (refresh_token) => request('/auth/logout', { method: 'POST', body: { refresh_token } }),
}

export const userApi = {
  me: (token) => request('/me', { token }),

  listCredentials: (token) => request('/me/credentials', { token }),
  putCredential: (token, provider, apiKey, label) =>
    request(`/me/credentials/${provider}`, {
      method: 'PUT',
      token,
      body: { api_key: apiKey, label },
    }),
  deleteCredential: (token, provider) =>
    request(`/me/credentials/${provider}`, { method: 'DELETE', token }),

  listKeys: (token) => request('/me/keys', { token }),
  createKey: (token, { label, requestsPerMinute, monthlyBudgetUsd, modelAllowlist }) =>
    request('/me/keys', {
      method: 'POST',
      token,
      body: {
        label,
        requests_per_minute: requestsPerMinute,
        monthly_budget_usd: monthlyBudgetUsd,
        model_allowlist: modelAllowlist,
      },
    }),
  setKeyStatus: (token, keyId, status) =>
    request(`/me/keys/${keyId}`, { method: 'PATCH', token, body: { status } }),

  usage: (token, from, to) => {
    const params = new URLSearchParams()
    if (from) params.set('from', from)
    if (to) params.set('to', to)
    const qs = params.toString()
    return request(`/me/usage${qs ? `?${qs}` : ''}`, { token })
  },
  logs: (token, limit = 25) => request(`/me/logs?${new URLSearchParams({ limit: String(limit) })}`, { token }),
  cacheStats: (token) => request('/me/cache-stats', { token }),
}
