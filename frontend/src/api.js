const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080'

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function request(path, { method = 'GET', token, body } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  const contentType = res.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await res.json() : await res.text()

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
  usage: (token, key, from, to) => {
    const params = new URLSearchParams({ key })
    if (from) params.set('from', from)
    if (to) params.set('to', to)
    return request(`/admin/usage?${params}`, { token })
  },
  logs: (token, key, limit = 25) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (key) params.set('key', key)
    return request(`/admin/logs?${params}`, { token })
  },
  cacheStats: (token, key) => {
    const params = new URLSearchParams()
    if (key) params.set('key', key)
    return request(`/admin/cache/stats?${params}`, { token })
  },
}
