import { useEffect, useState } from 'react'
import { useDispatch } from 'react-redux'
import { Plus, Trash2, Unlock, Loader2, Mail, AlertCircle, CheckCircle, XCircle } from 'lucide-react'
import { AppDispatch } from '../../app/store'
import { setConnections } from './connectionsSlice'
import { useGetConnectionsQuery, useStartGmailConnectionMutation, useEnableWriteAccessMutation, useDeleteConnectionMutation } from './connectionsSlice'

const STATUS_COLORS = {
  read_only: 'bg-blue-100 text-blue-800',
  write_enabled: 'bg-green-100 text-green-800',
  revoked: 'bg-red-100 text-red-800',
  connecting: 'bg-yellow-100 text-yellow-800',
}

const STATUS_LABELS: Record<string, string> = {
  read_only: 'Read Only',
  write_enabled: 'Read + Write',
  revoked: 'Revoked',
  connecting: 'Connecting...',
}

export function ConnectionsPage() {
  const dispatch = useDispatch<AppDispatch>()
  const { data: connections, isLoading, refetch } = useGetConnectionsQuery()
  const [startGmailConnection] = useStartGmailConnectionMutation()
  const [enableWriteAccess] = useEnableWriteAccessMutation()
  const [deleteConnection] = useDeleteConnectionMutation()

  const [connecting, setConnecting] = useState(false)

  // Sync connections from RTK Query to slice
  useEffect(() => {
    if (connections) {
      dispatch(setConnections(connections))
    }
  }, [connections, dispatch])

  const handleConnect = async () => {
    setConnecting(true)
    try {
      const result = await startGmailConnection().unwrap()
      window.location.href = result.consent_url
    } catch (error) {
      console.error('Failed to start connection:', error)
    } finally {
      setConnecting(false)
    }
  }

  const handleEnableWrite = async (connectionId: string) => {
    try {
      const result = await enableWriteAccess(connectionId).unwrap()
      window.location.href = result.consent_url
    } catch (error) {
      console.error('Failed to enable write access:', error)
    }
  }

  const handleDelete = async (connectionId: string) => {
    if (!confirm('Are you sure you want to revoke this Gmail connection?')) return
    try {
      await deleteConnection(connectionId).unwrap()
      refetch()
    } catch (error) {
      console.error('Failed to delete connection:', error)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Gmail Connections</h1>
          <p className="text-muted-foreground">Manage your Gmail account connections</p>
        </div>
        <button
          onClick={handleConnect}
          disabled={connecting}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-primary-foreground bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Connect Gmail
        </button>
      </div>

      {/* Info banner */}
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-800">
        <div className="flex items-start gap-3">
          <AlertCircle className="h-5 w-5 mt-0.5 flex-shrink-0" />
          <div className="text-sm">
            <p className="font-medium">Testing Mode — Weekly Re-consent Required</p>
            <p className="mt-1">
              This app runs in Google OAuth Testing mode. Refresh tokens expire after 7 days.
              You'll need to reconnect Gmail weekly. This is expected behavior, not a bug.
            </p>
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      ) : connections && connections.length === 0 ? (
        <div className="text-center py-12">
          <Mail className="mx-auto h-12 w-12 text-muted-foreground/50" />
          <h3 className="mt-4 text-lg font-medium">No Gmail Connections</h3>
          <p className="mt-2 text-muted-foreground">
            Connect your Gmail account to start using Email Agent.
          </p>
          <button
            onClick={handleConnect}
            disabled={connecting}
            className="mt-4 flex items-center justify-center gap-2 mx-auto px-4 py-2 text-sm font-medium text-primary-foreground bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            <Plus className="h-4 w-4" />
            Connect Gmail
          </button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {(connections ?? []).map((conn) => (
            <ConnectionCard
              key={conn.id}
              connection={conn}
              onEnableWrite={handleEnableWrite}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function ConnectionCard({
  connection,
  onEnableWrite,
  onDelete,
}: {
  connection: any
  onEnableWrite: (id: string) => void
  onDelete: (id: string) => void
}) {
  const statusColor = STATUS_COLORS[connection.status as keyof typeof STATUS_COLORS] || 'bg-gray-100 text-gray-800'
  const statusLabel = STATUS_LABELS[connection.status] || connection.status

  return (
    <div className="rounded-lg border border-border bg-card p-6 shadow-sm">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <Mail className="h-10 w-10 text-primary" />
          <div>
            <h3 className="font-semibold">{connection.provider_account}</h3>
            <p className="text-sm text-muted-foreground">Gmail</p>
          </div>
        </div>
        <span className={`px-2 py-1 text-xs font-medium rounded-full ${statusColor}`}>
          {statusLabel}
        </span>
      </div>

      <div className="mt-4 space-y-2 text-sm">
        <div className="flex items-center justify-between text-muted-foreground">
          <span>Scopes</span>
          <span className="font-mono">{connection.scopes.length} granted</span>
        </div>
        <div className="flex items-center justify-between text-muted-foreground">
          <span>Connected</span>
          <span>{new Date(connection.connected_at).toLocaleDateString()}</span>
        </div>
        {connection.last_used_at && (
          <div className="flex items-center justify-between text-muted-foreground">
            <span>Last Used</span>
            <span>{new Date(connection.last_used_at).toLocaleDateString()}</span>
          </div>
        )}
      </div>

      <div className="mt-4 flex gap-2">
        {connection.status === 'read_only' && (
          <button
            onClick={() => onEnableWrite(connection.id)}
            className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-sm font-medium text-primary-foreground bg-primary rounded-lg hover:bg-primary/90 transition-colors"
          >
            <Unlock className="h-4 w-4" />
            Enable Write Access
          </button>
        )}
        {connection.status === 'write_enabled' && (
          <span className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-sm font-medium text-green-700 bg-green-100 rounded-lg">
            <CheckCircle className="h-4 w-4" />
            Write Enabled
          </span>
        )}
        {connection.status === 'revoked' && (
          <span className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-sm font-medium text-red-700 bg-red-100 rounded-lg">
            <XCircle className="h-4 w-4" />
            Revoked
          </span>
        )}
        <button
          onClick={() => onDelete(connection.id)}
          className="px-3 py-2 text-sm font-medium text-red-700 bg-red-100 rounded-lg hover:bg-red-200 transition-colors"
          title="Revoke connection"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {/* Scope details */}
      <details className="mt-4">
        <summary className="text-xs text-muted-foreground cursor-pointer">Show scopes</summary>
        <ul className="mt-2 space-y-1 text-xs font-mono text-muted-foreground">
          {connection.scopes.map((scope: string) => (
            <li key={scope}>{scope}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}