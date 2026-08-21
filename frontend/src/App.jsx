import { AdminAuthProvider, useAdminAuth } from './AdminAuthContext'
import { UserAuthProvider, useUserAuth } from './UserAuthContext'
import Dashboard from './components/Dashboard'
import LoginForm from './components/LoginForm'
import SignupOrLoginForm from './components/user/SignupOrLoginForm'
import UserDashboard from './components/user/UserDashboard'
import { isOperatorPath, navigate, useCurrentPath } from './routing'

function AdminShell() {
  const { isAuthenticated } = useAdminAuth()
  if (isAuthenticated) return <Dashboard />

  // Logging out leaves you here on /operator rather than bouncing to the
  // tenant console - an operator who just logged out is far more likely to
  // be logging back in than to want a signup form.
  return (
    <div>
      <LoginForm />
      <p className="mode-switch">
        <button className="link" onClick={() => navigate('/')}>
          ← Tenant console
        </button>
      </p>
    </div>
  )
}

function UserShell() {
  const { isAuthenticated } = useUserAuth()
  return isAuthenticated ? <UserDashboard /> : <SignupOrLoginForm />
}

export default function App() {
  // Which console to show is derived from the URL, not component state, so a
  // refresh keeps you where you were and the operator console can be linked
  // or bookmarked directly.
  const path = useCurrentPath()

  return (
    <AdminAuthProvider>
      <UserAuthProvider>
        {isOperatorPath(path) ? <AdminShell /> : <UserShell />}
      </UserAuthProvider>
    </AdminAuthProvider>
  )
}
