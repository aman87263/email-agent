import { useEffect } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useGetConnectionsQuery } from '../features/connections/connectionsSlice'
import { LoginPage } from '../features/auth/LoginPage'
import { MainLayout } from '../components/MainLayout'
import { ChatPage } from '../features/agent/ChatPage'
import { ConnectionsPage } from '../features/connections/ConnectionsPage'
import { AuditPage } from '../features/audit/AuditPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { setAuthenticated, setUnauthenticated, setLoading } from '../features/auth/authSlice'
import { RootState, AppDispatch } from './store'

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { status } = useSelector((state: RootState) => state.auth)
  const dispatch = useDispatch<AppDispatch>()

  useEffect(() => {
    // Check session on mount
    const checkSession = async () => {
      dispatch(setLoading())
      try {
        const response = await fetch('/mail/api/v1/auth/session', {
          credentials: 'include',
        })
        if (response.ok) {
          const data = await response.json()
          dispatch(setAuthenticated({
            profile: data.user,
            roles: data.roles,
            csrfToken: data.csrfToken,
          }))
        } else {
          dispatch(setUnauthenticated())
        }
      } catch {
        dispatch(setUnauthenticated())
      }
    }
    checkSession()
  }, [dispatch])

  if (status === 'loading') {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  if (status !== 'authenticated') {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

function App() {
  const { data: connections } = useGetConnectionsQuery(undefined, { skip: false })
  const dispatch = useDispatch<AppDispatch>()

  // Sync connections from RTK Query to slice
  useEffect(() => {
    if (connections) {
      dispatch({ type: 'connections/setConnections', payload: connections })
    }
  }, [connections, dispatch])

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <PrivateRoute>
            <MainLayout>
              <Routes>
                <Route path="/" element={<Navigate to="/chat" replace />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/connections" element={<ConnectionsPage />} />
                <Route path="/audit" element={<AuditPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </MainLayout>
          </PrivateRoute>
        }
      />
    </Routes>
  )
}

export default App