import { createSlice, PayloadAction } from '@reduxjs/toolkit'
import { createApi, fetchBaseQuery } from '@reduxjs/toolkit/query/react'

export interface AuditEvent {
  id: string
  user_id: string
  connection_id: string | null
  action: string
  tool: string | null
  policy_decision: string
  impact_count: number
  impact_bytes: number
  label_delta_add: string[]
  label_delta_remove: string[]
  verification_outcome: string | null
  execution_state: string | null
  created_at: string
}

interface AuditState {
  page: number
  pageSize: number
  total: number
  events: AuditEvent[]
  filters: Record<string, any>
}

const initialState: AuditState = {
  page: 1,
  pageSize: 20,
  total: 0,
  events: [],
  filters: {},
}

const auditSlice = createSlice({
  name: 'audit',
  initialState,
  reducers: {
    setAuditEvents: (state, action: PayloadAction<{ events: AuditEvent[]; total: number; page: number }>) => {
      state.events = action.payload.events
      state.total = action.payload.total
      state.page = action.payload.page
    },
    setFilters: (state, action: PayloadAction<Record<string, any>>) => {
      state.filters = action.payload
      state.page = 1
    },
    setPage: (state, action: PayloadAction<number>) => {
      state.page = action.payload
    },
  },
})

export const { setAuditEvents, setFilters, setPage } = auditSlice.actions

export const auditApi = createApi({
  reducerPath: 'auditApi',
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
  tagTypes: ['Audit'],
  endpoints: (builder) => ({
    getAuditEvents: builder.query<{ events: AuditEvent[]; total: number }, { page: number; pageSize: number; filters?: Record<string, any> }>({
      query: ({ page, pageSize, filters }) => ({
        url: '/audit-events',
        params: { page, page_size: pageSize, ...filters },
      }),
    }),
  }),
})

export const { useGetAuditEventsQuery } = auditApi

export default auditSlice.reducer