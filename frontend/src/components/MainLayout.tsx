import { NavLink, useLocation } from 'react-router-dom'
import { useSelector } from 'react-redux'
import { LayoutDashboard, Mail, Settings, History, LogOut, Menu, X } from 'lucide-react'
import { RootState, AppDispatch } from '../app/store'
import { useDispatch } from 'react-redux'
import { setUnauthenticated } from '../features/auth/authSlice'

export function MainLayout({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const { profile, roles, csrfToken } = useSelector((state: RootState) => state.auth)
  const { sidebarOpen } = useSelector((state: RootState) => state.ui)
  const dispatch = useDispatch<AppDispatch>()

  const handleLogout = async () => {
    try {
      await fetch('/mail/api/auth/logout', {
        method: 'POST',
        headers: {
          'X-CSRF-Token': csrfToken || '',
        },
        credentials: 'include',
      })
    } finally {
      dispatch(setUnauthenticated())
    }
  }

  const navItems = [
    { path: '/chat', label: 'Chat', icon: Mail },
    { path: '/connections', label: 'Connections', icon: LayoutDashboard },
    { path: '/audit', label: 'Audit', icon: History },
    { path: '/settings', label: 'Settings', icon: Settings },
  ]

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 transform bg-card border-r border-border transition-transform duration-200 ease-in-out ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        } lg:relative lg:translate-x-0`}
      >
        <div className="flex h-full flex-col">
          {/* Header */}
          <div className="flex h-16 items-center justify-between px-4 border-b border-border">
            <h1 className="text-lg font-semibold">Email Agent</h1>
            <button
              className="lg:hidden p-2"
              onClick={() => dispatch({ type: 'ui/toggleSidebar' })}
              aria-label="Close sidebar"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Navigation */}
          <nav className="flex-1 p-4 space-y-1 overflow-y-auto">
            {navItems.map((item) => {
              const Icon = item.icon
              const isActive = location.pathname === item.path
              return (
                <NavLink
                  key={item.path}
                  to={item.path}
                  className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                  }`}
                >
                  <Icon className="h-5 w-5" />
                  {item.label}
                </NavLink>
              )
            })}
          </nav>

          {/* User menu */}
          <div className="p-4 border-t border-border">
            <div className="flex items-center gap-3 px-3 py-2">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{profile?.email}</p>
                <p className="text-xs text-muted-foreground truncate">{roles.join(', ') || 'User'}</p>
              </div>
              <button
                onClick={handleLogout}
                className="p-2 text-muted-foreground hover:text-foreground transition-colors"
                aria-label="Log out"
              >
                <LogOut className="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => dispatch({ type: 'ui/toggleSidebar' })}
          aria-hidden="true"
        />
      )}

      {/* Main content */}
      <main className="flex-1 lg:ml-0 min-w-0">
        <div className="flex h-full flex-col">
          {/* Top bar */}
          <header className="sticky top-0 z-30 flex h-16 items-center gap-4 px-4 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
            <button
              className="lg:hidden p-2"
              onClick={() => dispatch({ type: 'ui/toggleSidebar' })}
              aria-label="Open sidebar"
            >
              <Menu className="h-5 w-5" />
            </button>
            <h2 className="text-lg font-semibold">
              {navItems.find((item) => location.pathname === item.path)?.label || 'Email Agent'}
            </h2>
          </header>

          {/* Content */}
          <div className="flex-1 overflow-auto p-4 lg:p-6">
            {children}
          </div>
        </div>
      </main>
    </div>
  )
}