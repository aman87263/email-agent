import { useEffect, useMemo, useRef, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { Send, Loader2, Bot, User } from 'lucide-react'
import { RootState, AppDispatch } from '../../app/store'
import { setCurrentRun, addRun } from './runsSlice'
import { setProposal, clearActiveProposal } from '../approval/approvalsSlice'
import { ApprovalCard } from '../approval/ApprovalCard'
import { useStartGmailConnectionMutation } from '../connections/connectionsSlice'
import { cn } from '../../lib/utils'

interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  toolCalls?: Array<{ id: string; name: string; args: any }>
  toolResults?: Array<{ toolCallId: string; result: any; error?: string }>
  timestamp: Date
}

export function ChatPage() {
  const dispatch = useDispatch<AppDispatch>()
  const { currentRunId, transcripts, byId, lastSeq: lastSeqMap } = useSelector((state: RootState) => state.runs)
  const { activeProposalId, byId: proposals } = useSelector((state: RootState) => state.approvals)
  const { csrfToken } = useSelector((state: RootState) => state.auth)
  const { activeId: activeConnectionId } = useSelector((state: RootState) => state.connections)
  const [startGmailConnection] = useStartGmailConnectionMutation()

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  const currentRun = currentRunId ? byId[currentRunId] : null
  const currentTranscript = useMemo(
    () => (currentRunId ? transcripts[currentRunId] || [] : []),
    [transcripts, currentRunId]
  )
  const activeProposal = activeProposalId ? proposals[activeProposalId] : null

  // Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentTranscript])

  // Handle incoming events from transcript (replay or live)
  useEffect(() => {
    if (!currentRunId) return

    const newEvents = currentTranscript.filter((e) => e.seq > (lastSeqMap[currentRunId] || 0))
    for (const event of newEvents) {
      handleEvent(event)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTranscript, currentRunId])

  const handleEvent = (event: any) => {
    switch (event.event_type) {
      case 'TEXT_MESSAGE_CONTENT':
        setMessages((prev) => {
          const last = prev[prev.length - 1]
          if (last && last.role === 'assistant' && !last.toolCalls) {
            return [...prev.slice(0, -1), { ...last, content: last.content + event.payload.delta }]
          }
          return [...prev, { id: event.payload.messageId, role: 'assistant', content: event.payload.delta, timestamp: new Date() }]
        })
        break
      case 'TOOL_CALL_START':
        setMessages((prev) => [
          ...prev,
          { id: event.payload.toolCallId, role: 'tool', content: `Calling ${event.payload.toolName}...`, toolCalls: [event.payload], timestamp: new Date() },
        ])
        break
      case 'TOOL_CALL_RESULT':
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.toolCalls?.some((tc) => tc.id === event.payload.toolCallId))
          if (idx >= 0) {
            const newMessages = [...prev]
            newMessages[idx] = {
              ...newMessages[idx],
              content: event.payload.error ? `Error: ${event.payload.error}` : `Result: ${JSON.stringify(event.payload.result).slice(0, 200)}`,
              toolResults: [...(newMessages[idx].toolResults || []), event.payload],
            }
            return newMessages
          }
          return prev
        })
        break
      case 'RUN_FINISHED':
        setIsStreaming(false)
        if (event.payload.outcome?.type === 'interrupt') {
          const interrupt = event.payload.outcome.interrupts[0]
          if (interrupt) {
            dispatch(setProposal({
              proposalId: interrupt.payload.proposalId,
              tool: interrupt.payload.tool,
              estimatedCount: interrupt.payload.estimatedCount,
              estimatedBytes: interrupt.payload.estimatedBytes,
              sampleRefs: interrupt.payload.sampleRefs,
              expiresAt: interrupt.payload.expiresAt,
              actionDigest: interrupt.payload.actionDigest,
              addLabels: interrupt.payload.labelDeltaAdd,
              removeLabels: interrupt.payload.labelDeltaRemove,
            }))
          }
        }
        break
      case 'RUN_STARTED':
        dispatch(addRun({
          run_id: event.payload.runId,
          thread_id: event.payload.threadId,
          status: 'running',
          outcome: null,
          created_at: new Date().toISOString(),
          completed_at: null,
        }))
        dispatch(setCurrentRun(event.payload.runId))
        break
    }
  }

  const sendMessage = async (content: string) => {
    if (!content.trim() || isStreaming) return
    if (!activeConnectionId) {
      // Prompt to connect Gmail
      const result = await startGmailConnection().unwrap()
      window.location.href = result.consent_url
      return
    }

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      timestamp: new Date(),
    }
    setMessages((prev) => [...prev, userMessage])
    setInput('')

    // Create new run
    const threadId = currentRun?.thread_id
    setIsStreaming(true)

    abortControllerRef.current = new AbortController()

    try {
      const response = await fetch('/mail/api/v1/agent/runs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken || '',
        },
        credentials: 'include',
        body: JSON.stringify({
          thread_id: threadId,
          messages: [{ role: 'user', content }],
          tools: [], // Will be populated by backend
        }),
        signal: abortControllerRef.current.signal,
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      // Stream events
      const reader = response.body?.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (reader) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6))
              handleEvent({ event_type: event.type, payload: event })
            } catch {
              // Ignore parse errors
            }
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: 'assistant', content: `Error: ${error.message}`, timestamp: new Date() }])
      }
      setIsStreaming(false)
    }
  }

  const handleApprove = async (proposalId: string, approved: boolean) => {
    dispatch(clearActiveProposal())

    const threadId = currentRun?.thread_id
    setIsStreaming(true)

    try {
      const response = await fetch('/mail/api/v1/agent/runs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken || '',
        },
        credentials: 'include',
        body: JSON.stringify({
          thread_id: threadId,
          resume: [{
            interruptId: proposalId,
            status: approved ? 'approved' : 'rejected',
            payload: { approved },
          }],
        }),
      })

      if (!response.ok) throw new Error(`HTTP ${response.status}`)

      // Stream events...
      const reader = response.body?.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (reader) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6))
              handleEvent({ event_type: event.type, payload: event })
            } catch {
              // Malformed SSE frame — skip it
            }
          }
        }
      }
    } catch (error) {
      if (error instanceof Error) {
        setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: 'assistant', content: `Error: ${error.message}`, timestamp: new Date() }])
      }
      setIsStreaming(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Connection status */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-background/50 backdrop-blur">
        <div className="flex items-center gap-4 text-sm">
          <span className="flex items-center gap-1">
            <span className={cn('h-2 w-2 rounded-full', activeConnectionId ? 'bg-green-500' : 'bg-red-500')} />
            {activeConnectionId ? 'Gmail Connected' : 'No Gmail Connection'}
          </span>
        </div>
        {isStreaming && (
          <div className="flex items-center gap-2 text-sm text-primary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Processing...
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <Bot className="h-12 w-12 mb-4 opacity-50" />
            <h3 className="text-lg font-medium">Welcome to Email Agent</h3>
            <p className="text-sm mt-1">Ask me about your emails, search for messages, or request actions like archiving or labeling.</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2 max-w-xs">
              <ExamplePrompt prompt="Show invoices from last month" onSelect={sendMessage} />
              <ExamplePrompt prompt="Find emails from GitHub notifications" onSelect={sendMessage} />
              <ExamplePrompt prompt="Summarize unread emails from today" onSelect={sendMessage} />
              <ExamplePrompt prompt="Archive all promotional emails older than 6 months" onSelect={sendMessage} />
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Approval card */}
      {activeProposal && (
        <ApprovalCard proposal={activeProposal} onApprove={handleApprove} onReject={() => handleApprove(activeProposal.proposalId, false)} />
      )}

      {/* Input */}
      <div className="border-t border-border bg-background/50 backdrop-blur p-4">
        <div className="flex gap-2 max-w-4xl mx-auto">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                sendMessage(input)
              }
            }}
            placeholder={activeConnectionId ? 'Ask about your emails...' : 'Connect Gmail first'}
            disabled={isStreaming || !activeConnectionId}
            className="flex-1 min-h-[44px] max-h-48 px-4 py-2 text-sm border border-input bg-background rounded-lg resize-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            rows={1}
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || isStreaming || !activeConnectionId}
            className="flex items-center justify-center px-4 py-2 text-sm font-medium text-primary-foreground bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors"
            aria-label="Send message"
          >
            <Send className="h-5 w-5" />
          </button>
        </div>
        <p className="mt-2 text-center text-xs text-muted-foreground">
          Press Enter to send, Shift+Enter for new line
        </p>
      </div>
    </div>
  )
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'

  return (
    <div className={cn('flex gap-3', isUser && 'flex-row-reverse')}>
      <div className={cn('flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-xs font-medium', isUser ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground')}>
        {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>
      <div className={cn('flex-1 max-w-[80%]', isUser ? 'text-right' : '')}>
        <div className={cn('inline-block px-4 py-2 rounded-2xl text-sm', isUser ? 'bg-primary text-primary-foreground rounded-tr-none' : 'bg-muted rounded-tl-none')}>
          <pre className="whitespace-pre-wrap">{message.content}</pre>
        </div>
        {message.toolCalls && message.toolCalls.map((tc) => (
          <div key={tc.id} className="mt-1 text-xs text-muted-foreground font-mono">
            🔧 {tc.name}({JSON.stringify(tc.args).slice(0, 100)})
          </div>
        ))}
        <div className="mt-1 text-xs text-muted-foreground">
          {message.timestamp.toLocaleTimeString()}
        </div>
      </div>
    </div>
  )
}

function ExamplePrompt({ prompt, onSelect }: { prompt: string; onSelect: (prompt: string) => void }) {
  return (
    <button
      onClick={() => onSelect(prompt)}
      className="text-left px-3 py-2 text-sm border border-border rounded-lg hover:bg-accent transition-colors"
    >
      {prompt}
    </button>
  )
}
