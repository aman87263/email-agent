import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Mail, Lock } from 'lucide-react'

export function LoginPage() {
  const navigate = useNavigate()

  useEffect(() => {
    // Check if already authenticated
    const checkAuth = async () => {
      try {
        const response = await fetch('/mail/api/v1/auth/session', {
          credentials: 'include',
        })
        if (response.ok) {
          navigate('/chat', { replace: true })
        }
      } catch {
        // Not authenticated, stay on login page
      }
    }
    checkAuth()
  }, [navigate])

  const handleLogin = () => {
    // Redirect to backend auth endpoint which starts Keycloak OIDC flow
    window.location.href = '/mail/api/auth/login'
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center">
          <Mail className="mx-auto h-12 w-12 text-primary" />
          <h1 className="mt-4 text-3xl font-bold tracking-tight">Email Agent</h1>
          <p className="mt-2 text-muted-foreground">
            Sign in to access your Gmail with AI assistance
          </p>
        </div>

        <div className="rounded-lg border border-border bg-card p-6 shadow-sm">
          <button
            onClick={handleLogin}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Lock className="h-5 w-5" />
            Sign in with Keycloak
          </button>

          <div className="mt-6 space-y-3 text-sm text-muted-foreground">
            <p className="text-center">
              This is a personal project running in Testing mode.
            </p>
            <p className="text-center">
              Gmail connections require weekly re-consent (7-day token expiry).
            </p>
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground">
          DuckDNS deployment · Shared Keycloak · OCI Always Free ARM64
        </p>
      </div>
    </div>
  )
}