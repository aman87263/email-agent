import { useState } from 'react'
import { AlertTriangle, Trash2, Archive, Tag, Check, Loader2 } from 'lucide-react'

interface ProposalSnapshot {
  proposalId: string
  tool: string
  estimatedCount: number
  estimatedBytes: number
  sampleRefs: Array<{
    message_id: string
    thread_id: string
    size_estimate: number
  }>
  expiresAt: string
  actionDigest: string
  addLabels?: string[]
  removeLabels?: string[]
}

interface ApprovalCardProps {
  proposal: ProposalSnapshot
  onApprove: (proposalId: string, approved: boolean) => void
  onReject: () => void
}

export function ApprovalCard({ proposal, onApprove, onReject }: ApprovalCardProps) {
  const [confirmText, setConfirmText] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const requiresTypedConfirm = proposal.estimatedCount > 50

  const toolIcons: Record<string, React.ComponentType<{ className?: string }>> = {
    propose_trash: Trash2,
    propose_archive: Archive,
    propose_labels: Tag,
  }

  const Icon = toolIcons[proposal.tool] || AlertTriangle

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const handleApprove = async () => {
    if (requiresTypedConfirm) {
      const expected = proposal.tool === 'propose_trash' ? 'TRASH' : proposal.estimatedCount.toString()
      if (confirmText !== expected) return
    }
    setIsSubmitting(true)
    await onApprove(proposal.proposalId, true)
    setIsSubmitting(false)
  }

  const handleReject = async () => {
    setIsSubmitting(true)
    await onReject()
    setIsSubmitting(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="w-full max-w-2xl rounded-lg border border-border bg-card shadow-xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-border bg-destructive/10">
          <Icon className="h-6 w-6 text-destructive" />
          <div>
            <h3 className="font-semibold text-lg">{proposal.tool === 'propose_trash' ? 'Move to Trash' : proposal.tool === 'propose_archive' ? 'Archive Messages' : 'Change Labels'}</h3>
            <p className="text-sm text-muted-foreground">This action requires your approval</p>
          </div>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4 max-h-[60vh] overflow-y-auto">
          {/* Warning for trash */}
          {proposal.tool === 'propose_trash' && (
            <div className="flex items-start gap-3 p-4 rounded-lg bg-amber-50 border border-amber-200 text-amber-800">
              <AlertTriangle className="h-5 w-5 mt-0.5 flex-shrink-0" />
              <div>
                <p className="font-medium">Trash does not free storage</p>
                <p className="text-sm mt-1">Messages in Trash still count against your Google storage quota until Trash is emptied. Emptying Trash is permanent deletion.</p>
              </div>
            </div>
          )}

          {/* Stats */}
          <div className="grid grid-cols-2 gap-4">
            <div className="p-4 rounded-lg border border-border">
              <p className="text-sm text-muted-foreground">Messages</p>
              <p className="text-2xl font-bold">{proposal.estimatedCount.toLocaleString()}</p>
            </div>
            <div className="p-4 rounded-lg border border-border">
              <p className="text-sm text-muted-foreground">Bytes Affected</p>
              <p className="text-2xl font-bold">{formatBytes(proposal.estimatedBytes)}</p>
            </div>
          </div>

          {/* Label changes */}
          {(proposal.addLabels?.length || proposal.removeLabels?.length) && (
            <div className="space-y-2">
              <p className="text-sm font-medium">Label Changes</p>
              <div className="flex flex-wrap gap-2">
                {proposal.addLabels?.map((label) => (
                  <span key={label} className="px-2 py-1 text-xs bg-green-100 text-green-800 rounded">+ {label}</span>
                ))}
                {proposal.removeLabels?.map((label) => (
                  <span key={label} className="px-2 py-1 text-xs bg-red-100 text-red-800 rounded">− {label}</span>
                ))}
              </div>
            </div>
          )}

          {/* Sample messages */}
          {proposal.sampleRefs.length > 0 && (
            <div className="space-y-2">
              <p className="text-sm font-medium">Sample Messages ({proposal.sampleRefs.length} of {proposal.estimatedCount})</p>
              <div className="max-h-48 overflow-y-auto space-y-2">
                {proposal.sampleRefs.map((ref) => (
                  <div key={ref.message_id} className="p-3 rounded border border-border bg-muted/50">
                    <p className="font-mono text-xs truncate">Message {ref.message_id}</p>
                    <p className="text-xs text-muted-foreground">{formatBytes(ref.size_estimate)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Expiry */}
          <p className="text-xs text-muted-foreground">
            Expires: {new Date(proposal.expiresAt).toLocaleString()}
          </p>

          {/* Typed confirmation for large batches */}
          {requiresTypedConfirm && (
            <div className="space-y-2 p-4 rounded-lg border border-border bg-muted/50">
              <p className="text-sm font-medium">
                Type <code className="px-1.5 py-0.5 bg-background rounded font-mono">
                  {proposal.tool === 'propose_trash' ? 'TRASH' : proposal.estimatedCount.toString()}
                </code> to confirm
              </p>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={proposal.tool === 'propose_trash' ? 'TRASH' : proposal.estimatedCount.toString()}
                className="w-full px-3 py-2 text-sm border border-input bg-background rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring font-mono"
                autoFocus
              />
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-border">
          <button
            onClick={handleReject}
            disabled={isSubmitting}
            className="px-4 py-2 text-sm font-medium text-foreground bg-background border border-input rounded-lg hover:bg-accent transition-colors disabled:opacity-50"
          >
            Reject
          </button>
          <button
            onClick={handleApprove}
            disabled={isSubmitting || (requiresTypedConfirm && confirmText !== (proposal.tool === 'propose_trash' ? 'TRASH' : proposal.estimatedCount.toString()))}
            className="px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive rounded-lg hover:bg-destructive/90 transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {proposal.tool === 'propose_trash' ? 'Move to Trash' : proposal.tool === 'propose_archive' ? 'Archive' : 'Apply Labels'}
          </button>
        </div>
      </div>
    </div>
  )
}
