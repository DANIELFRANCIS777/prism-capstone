import { AuthProvider, useAuth } from './AuthContext'
import Dashboard from './components/Dashboard'
import LoginForm from './components/LoginForm'

function AppShell() {
  const { isAuthenticated } = useAuth()
  return isAuthenticated ? <Dashboard /> : <LoginForm />
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  )
}
