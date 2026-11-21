import { useSelector } from 'react-redux'
import { User, Shield, Palette, Key, Trash2 } from 'lucide-react'
import { RootState } from '../../app/store'
import { cn } from '../../lib/utils'

export function SettingsPage() {
  const { profile, roles } = useSelector((state: RootState) => state.auth)
  const { theme } = useSelector((state: RootState) => state.ui)

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground">Manage your account and preferences</p>
      </div>

      {/* Account */}
      <section className="rounded-lg border border-border bg-card p-6 space-y-6">
        <div className="flex items-center gap-4">
          <div className="h-16 w-16 rounded-full bg-primary/10 flex items-center justify-center">
            <User className="h-8 w-8 text-primary" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">{profile?.email}</h2>
            <p className="text-sm text-muted-foreground">{roles.join(', ') || 'User'}</p>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <label className="text-sm font-medium">Keycloak Subject</label>
            <code className="text-xs font-mono text-muted-foreground break-all bg-muted px-2 py-1 rounded">
              {profile?.keycloak_subject}
            </code>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Roles</label>
            <div className="flex flex-wrap gap-2">
              {roles.map((role) => (
                <span key={role} className="px-2 py-1 text-xs bg-muted rounded">
                  {role}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Appearance */}
      <section className="rounded-lg border border-border bg-card p-6 space-y-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Palette className="h-5 w-5" />
          Appearance
        </h2>

        <div className="grid gap-4 md:grid-cols-3">
          {(['light', 'dark', 'system'] as const).map((t) => (
            <button
              key={t}
              className={cn(
                'p-4 border-2 rounded-lg transition-colors text-left',
                theme === t
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:border-primary/50'
              )}
            >
              <div className="font-medium capitalize">{t}</div>
              <div className="text-sm text-muted-foreground mt-1">
                {t === 'light' && 'Always light mode'}
                {t === 'dark' && 'Always dark mode'}
                {t === 'system' && 'Match system preference'}
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* Security */}
      <section className="rounded-lg border border-border bg-card p-6 space-y-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Shield className="h-5 w-5" />
          Security
        </h2>

        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="font-medium">Session</p>
              <p className="text-sm text-muted-foreground">HttpOnly cookie, 8h idle / 12h absolute timeout</p>
            </div>
            <span className="px-2 py-1 text-xs bg-green-100 text-green-800 rounded">Active</span>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <p className="font-medium">CSRF Protection</p>
              <p className="text-sm text-muted-foreground">Synchronizer token pattern on all mutations</p>
            </div>
            <span className="px-2 py-1 text-xs bg-green-100 text-green-800 rounded">Enabled</span>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <p className="font-medium">Gmail Tokens</p>
              <p className="text-sm text-muted-foreground">Encrypted at rest with envelope encryption (AES-GCM)</p>
            </div>
            <span className="px-2 py-1 text-xs bg-green-100 text-green-800 rounded">Encrypted</span>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <p className="font-medium">Keycloak Tokens</p>
              <p className="text-sm text-muted-foreground">Not stored — only sub, email, roles kept in session</p>
            </div>
            <span className="px-2 py-1 text-xs bg-green-100 text-green-800 rounded">Not Stored</span>
          </div>
        </div>
      </section>

      {/* Data & Privacy */}
      <section className="rounded-lg border border-border bg-card p-6 space-y-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Key className="h-5 w-5" />
          Data & Privacy
        </h2>

        <div className="space-y-4 text-sm">
          <p className="text-muted-foreground">
            <strong>No email bodies stored.</strong> Only message IDs, thread IDs, labels, size estimates, and content hashes are persisted.
          </p>
          <p className="text-muted-foreground">
            <strong>Proposal items</strong> (message IDs + prior labels) are stored for exact execution and undo, purged after 7 days.
          </p>
          <p className="text-muted-foreground">
            <strong>Audit events</strong> contain pseudonymous IDs only — no subjects, no bodies.
          </p>
          <p className="text-muted-foreground">
            <strong>Retention:</strong> Agent events 30 days, proposals/executions/audit retained, email bodies never stored.
          </p>
        </div>
      </section>

      {/* Danger Zone */}
      <section className="rounded-lg border border-red-200 bg-red-50 p-6 space-y-4">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-red-800">
          <Trash2 className="h-5 w-5" />
          Danger Zone
        </h2>

        <p className="text-sm text-red-700">
          These actions are irreversible. Use with caution.
        </p>

        <div className="flex gap-2">
          <button className="px-4 py-2 text-sm font-medium text-red-700 bg-red-100 border border-red-300 rounded-lg hover:bg-red-200 transition-colors">
            <Trash2 className="h-4 w-4 mr-2" />
            Revoke All Sessions
          </button>
          <button className="px-4 py-2 text-sm font-medium text-red-700 bg-red-100 border border-red-300 rounded-lg hover:bg-red-200 transition-colors">
            Delete All Data
          </button>
        </div>
      </section>
    </div>
  )
}