import { createSlice, PayloadAction } from '@reduxjs/toolkit'
import { createApi, fetchBaseQuery } from '@reduxjs/toolkit/query/react'

export interface RunEvent {
  seq: number
  event_type: string
  payload: any
}

export interface AgentRun {
  run_id: string
  thread_id: string
  status: string
  outcome: any | null
  created_at: string
  completed_at: string | null
}

interface RunsState {
  byId: Record<string, AgentRun>
  currentRunId: string | null
  transcripts: Record<string, RunEvent[]> // Capped ring buffer per run
  lastSeq: Record<string, number>
}

const initialState: RunsState = {
  byId: {},
  currentRunId: null,
  transcripts: {},
  lastSeq: {},
}

const MAX_TRANSCRIPT_LENGTH = 100

const runsSlice = createSlice({
  name: 'runs',
  initialState,
  reducers: {
    setCurrentRun: (state, action: PayloadAction<string | null>) => {
      state.currentRunId = action.payload
    },
    addRun: (state, action: PayloadAction<AgentRun>) => {
      state.byId[action.payload.run_id] = action.payload
      state.transcripts[action.payload.run_id] = []
      state.lastSeq[action.payload.run_id] = 0
    },
    updateRunStatus: (state, action: PayloadAction<{ runId: string; status: string; outcome?: any; completedAt?: string }>) => {
      const { runId, status, outcome, completedAt } = action.payload
      if (state.byId[runId]) {
        state.byId[runId].status = status
        if (outcome) state.byId[runId].outcome = outcome
        if (completedAt) state.byId[runId].completed_at = completedAt
      }
    },
    appendEvents: (state, action: PayloadAction<{ runId: string; events: RunEvent[] }>) => {
      const { runId, events } = action.payload
      if (!state.transcripts[runId]) {
        state.transcripts[runId] = []
      }
      state.transcripts[runId].push(...events)

      // Cap the transcript
      if (state.transcripts[runId].length > MAX_TRANSCRIPT_LENGTH) {
        state.transcripts[runId] = state.transcripts[runId].slice(-MAX_TRANSCRIPT_LENGTH)
      }

      // Update lastSeq
      const lastEvent = events[events.length - 1]
      if (lastEvent) {
        state.lastSeq[runId] = lastEvent.seq
      }
    },
    setTranscript: (state, action: PayloadAction<{ runId: string; events: RunEvent[] }>) => {
      state.transcripts[action.payload.runId] = action.payload.events
      const lastEvent = action.payload.events[action.payload.events.length - 1]
      if (lastEvent) {
        state.lastSeq[action.payload.runId] = lastEvent.seq
      }
    },
    clearRun: (state, action: PayloadAction<string>) => {
      delete state.byId[action.payload]
      delete state.transcripts[action.payload]
      delete state.lastSeq[action.payload]
      if (state.currentRunId === action.payload) {
        state.currentRunId = null
      }
    },
  },
})

export const { setCurrentRun, addRun, updateRunStatus, appendEvents, setTranscript, clearRun } = runsSlice.actions

// RTK Query API for runs
export const runsApi = createApi({
  reducerPath: 'runsApi',
  baseQuery: fetchBaseQuery({
    baseUrl: '/mail/api/v1',
    prepareHeaders: (headers, { getState }) => {
      const state = getState() as any
      const csrfToken = state.auth.csrfToken
      if (csrfToken) {
        headers.set('X-CSRF-Token', csrfToken)
      }
      return headers
    },
    credentials: 'include',
  }),
  tagTypes: ['Runs'],
  endpoints: (builder) => ({
    getRun: builder.query<AgentRun, string>({
      query: (runId) => `/agent/runs/${runId}`,
      providesTags: (result, error, runId) => [{ type: 'Runs', id: runId }],
    }),
    getRunEvents: builder.query<RunEvent[], { runId: string; after?: number }>({
      query: ({ runId, after }) => `/agent/runs/${runId}/events${after ? `?after=${after}` : ''}`,
    }),
    cancelRun: builder.mutation<void, string>({
      query: (runId) => ({ url: `/agent/runs/${runId}/cancel`, method: 'POST' }),
      invalidatesTags: ['Runs'],
    }),
  }),
})

export const { useGetRunQuery, useGetRunEventsQuery, useCancelRunMutation } = runsApi

export default runsSlice.reducer