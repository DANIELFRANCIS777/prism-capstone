import { userAuthApi } from './api'
import { createAuthContext } from './createAuthContext'

export const { AuthProvider: UserAuthProvider, useAuth: useUserAuth } = createAuthContext({
  storageKey: 'prism_user_tokens',
  api: userAuthApi,
})
