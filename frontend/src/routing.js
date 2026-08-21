import { useEffect, useState } from 'react'

// The operator console lives at its own path rather than behind a link in
// the tenant UI. Two reasons: a refresh has to land you back where you were
// (view state held in useState silently reset to the tenant console), and
// tenants have no business seeing an entry point to a console they can never
// use. It is not a security boundary - every /admin/* route is JWT-scoped
// server-side - it just keeps an operator-only surface out of the product.
export const OPERATOR_PATH = '/operator'

export function useCurrentPath() {
  const [path, setPath] = useState(() => window.location.pathname)

  useEffect(() => {
    const sync = () => setPath(window.location.pathname)
    // popstate covers the browser's own back/forward; navigate() below
    // dispatches the same event so programmatic moves take the same path.
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])

  return path
}

export function navigate(path) {
  if (window.location.pathname === path) return
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function isOperatorPath(path) {
  return path.startsWith(OPERATOR_PATH)
}
