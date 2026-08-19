import { authApi } from './api'
import { createAuthContext } from './createAuthContext'

// storageKey unchanged from the original AuthContext.jsx so existing
// operator sessions survive this file's rename.
export const { AuthProvider: AdminAuthProvider, useAuth: useAdminAuth } = createAuthContext({
  storageKey: 'prism_admin_tokens',
  api: authApi,
})
