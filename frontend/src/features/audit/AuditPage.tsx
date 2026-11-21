import { useEffect, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { ChevronLeft, ChevronRight, Download, Loader2, Calendar, Search, Trash2, Archive, Tag, Shield } from 'lucide-react'
import { RootState, AppDispatch } from '../../app/store'
import { setAuditEvents, setFilters, setPage } from './auditSlice'
import { useGetAuditEventsQuery } from './auditSlice'
import { cn } from '../../lib/utils'

const ACTION_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  execute: Shield,
  propose: Tag,
  read: Search,
  delete: Trash2,
  archive: Archive,
}

const POLICY_COLORS: Record<string, string> = {
  allow: 'bg-green-100 text-green-800',
  deny: 'bg-red-100 text-red-800',
  require_approval: 'bg-amber-100 text-amber-800',
}

export function AuditPage() {
  const dispatch = useDispatch<AppDispatch>()
  const { page, pageSize, filters } = useSelector((state: RootState) => state.audit)
  const { data, isLoading } = useGetAuditEventsQuery({ page, pageSize, filters })

  const [dateRange, setDateRange] = useState<{ from: Date | undefined; to: Date | undefined }>({
    from: undefined,
    to: undefined,
  })

  useEffect(() => {
    if (data) {
      dispatch(setAuditEvents({ events: data.events, total: data.total, page }))
    }
  }, [data, page, dispatch])

  const handleFilterChange = (newFilters: Record<string, any>) => {
    dispatch(setFilters(newFilters))
  }

  const handleDateChange = (range: { from: Date | undefined; to: Date | undefined }) => {
    setDateRange(range)
    const newFilters = { ...filters }
    if (range.from) newFilters.from = range.from.toISOString()
    if (range.to) newFilters.to = range.to.toISOString()
    dispatch(setFilters(newFilters))
  }

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const totalPages = data ? Math.ceil(data.total / pageSize) : 1

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Audit Log</h1>
          <p className="text-muted-foreground">Security and action audit trail</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="flex items-center gap-2 px-4 py-2 text-sm font-medium border border-input bg-background rounded-lg hover:bg-accent transition-colors">
            <Download className="h-4 w-4" />
            Export
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center gap-4">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search actions..."
              value={(filters.search as string) || ''}
              onChange={(e) => handleFilterChange({ ...filters, search: e.target.value || undefined })}
              className="w-full pl-10 pr-4 py-2 text-sm border border-input bg-background rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>

          <div className="flex items-center gap-2">
            <Calendar className="h-4 w-4 text-muted-foreground" />
            <input
              type="date"
              value={dateRange.from ? dateRange.from.toISOString().split('T')[0] : ''}
              onChange={(e) => handleDateChange({ ...dateRange, from: e.target.value ? new Date(e.target.value) : undefined })}
              className="px-3 py-2 text-sm border border-input bg-background rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <span className="text-muted-foreground">to</span>
            <input
              type="date"
              value={dateRange.to ? dateRange.to.toISOString().split('T')[0] : ''}
              onChange={(e) => handleDateChange({ ...dateRange, to: e.target.value ? new Date(e.target.value) : undefined })}
              className="px-3 py-2 text-sm border border-input bg-background rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>

          <select
            value={(filters.policy_decision as string) || ''}
            onChange={(e) => handleFilterChange({ ...filters, policy_decision: e.target.value || undefined })}
            className="px-3 py-2 text-sm border border-input bg-background rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">All Decisions</option>
            <option value="allow">Allow</option>
            <option value="deny">Deny</option>
            <option value="require_approval">Require Approval</option>
          </select>

          <select
            value={(filters.action as string) || ''}
            onChange={(e) => handleFilterChange({ ...filters, action: e.target.value || undefined })}
            className="px-3 py-2 text-sm border border-input bg-background rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">All Actions</option>
            <option value="execute">Execute</option>
            <option value="propose">Propose</option>
            <option value="read">Read</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </div>
        ) : data && data.events.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            No audit events found
          </div>
        ) : data ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border bg-muted/50">
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Time</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Action</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Tool</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Decision</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Impact</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Labels</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Verification</th>
                  </tr>
                </thead>
                <tbody>
                  {data?.events.map((event) => (
                    <tr key={event.id} className="border-b border-border hover:bg-muted/50">
                      <td className="px-4 py-3 text-sm font-mono text-muted-foreground">
                        {new Date(event.created_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className="flex items-center gap-1">
                          {ACTION_ICONS[event.action] && (() => {
                            const Icon = ACTION_ICONS[event.action];
                            return <Icon className="h-4 w-4" />;
                          })()}
                          {event.action}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm font-mono text-muted-foreground">
                        {event.tool || '—'}
                      </td>
                      <td className="px-4 py-3">
                        <span className={cn('px-2 py-0.5 text-xs font-medium rounded', POLICY_COLORS[event.policy_decision] || 'bg-gray-100 text-gray-800')}>
                          {event.policy_decision.replace('_', ' ')}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        {event.impact_count > 0 && (
                          <span className="font-mono">{event.impact_count} msgs · {formatBytes(event.impact_bytes)}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <div className="flex gap-1">
                          {event.label_delta_add.map((l: string) => (
                            <span key={l} className="px-1.5 py-0.5 text-xs bg-green-100 text-green-800 rounded">+{l}</span>
                          ))}
                          {event.label_delta_remove.map((l: string) => (
                            <span key={l} className="px-1.5 py-0.5 text-xs bg-red-100 text-red-800 rounded">−{l}</span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        {event.verification_outcome && (
                          <span className={cn('px-2 py-0.5 text-xs font-medium rounded',
                            event.verification_outcome === 'verified' && 'bg-green-100 text-green-800',
                            event.verification_outcome === 'failed' && 'bg-red-100 text-red-800',
                            event.verification_outcome === 'indeterminate' && 'bg-amber-100 text-amber-800',
                          )}>
                            {event.verification_outcome}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between px-4 py-3 border-t border-border">
                <p className="text-sm text-muted-foreground">
                  Page {page} of {totalPages} · {data?.total} total
                </p>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => dispatch(setPage(page - 1))}
                    disabled={page === 1}
                    className="p-2 text-sm border border-input bg-background rounded hover:bg-accent disabled:opacity-50"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => dispatch(setPage(page + 1))}
                    disabled={page === totalPages}
                    className="p-2 text-sm border border-input bg-background rounded hover:bg-accent disabled:opacity-50"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              </div>
            )}
          </>
        ) : null}
      </div>
    </div>
  )
}