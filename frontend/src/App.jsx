import { useState } from 'react'
import { AdminAuthProvider, useAdminAuth } from './AdminAuthContext'
import { UserAuthProvider, useUserAuth } from './UserAuthContext'
import Dashboard from './components/Dashboard'
import LoginForm from './components/LoginForm'
import SignupOrLoginForm from './components/user/SignupOrLoginForm'
import UserDashboard from './components/user/UserDashboard'

function AdminShell({ onExit }) {
  const { isAuthenticated } = useAdminAuth()
  return (
    <div>
      {isAuthenticated ? (
        <Dashboard />
      ) : (
        <>
          <LoginForm />
          <p className="mode-switch">
            <button className="link" onClick={onExit}>
              ← Back to sign in
            </button>
          </p>
        </>
      )}
    </div>
  )
}

function UserShell({ onOperatorLogin }) {
  const { isAuthenticated } = useUserAuth()
  return isAuthenticated ? (
    <UserDashboard onOperatorLogin={onOperatorLogin} />
  ) : (
    <div>
      <SignupOrLoginForm />
      <p className="mode-switch">
        <button className="link" onClick={onOperatorLogin}>
          Operator login →
        </button>
      </p>
    </div>
  )
}

export default function App() {
  // Tenant console is the default surface (open-source-SaaS self-serve
  // model); the platform operator's console is one click away, not the
  // other way around, since operators are a small fraction of visitors.
  const [view, setView] = useState('user') // 'user' | 'admin'

  return (
    <AdminAuthProvider>
      <UserAuthProvider>
        {view === 'admin' ? (
          <AdminShell onExit={() => setView('user')} />
        ) : (
          <UserShell onOperatorLogin={() => setView('admin')} />
        )}
      </UserAuthProvider>
    </AdminAuthProvider>
  )
}
