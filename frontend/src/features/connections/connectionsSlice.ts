import { createSlice, PayloadAction } from '@reduxjs/toolkit'
import { createApi, fetchBaseQuery } from '@reduxjs/toolkit/query/react'

interface Connection {
  id: string
  provider_account: string
  scopes: string[]
  status: string
  connected_at: string
  last_used_at: string | null
}

interface ConnectionsState {
  byId: Record<string, Connection>
  activeId: string | null
}

const initialState: ConnectionsState = {
  byId: {},
  activeId: null,
}

const connectionsSlice = createSlice({
  name: 'connections',
  initialState,
  reducers: {
    setConnections: (state, action: PayloadAction<Connection[]>) => {
      state.byId = {}
      action.payload.forEach((conn) => {
        state.byId[conn.id] = conn
      })
      if (action.payload.length > 0 && !state.activeId) {
        state.activeId = action.payload[0].id
      }
    },
    setActiveConnection: (state, action: PayloadAction<string | null>) => {
      state.activeId = action.payload
    },
    addConnection: (state, action: PayloadAction<Connection>) => {
      state.byId[action.payload.id] = action.payload
      if (!state.activeId) {
        state.activeId = action.payload.id
      }
    },
    updateConnection: (state, action: PayloadAction<Partial<Connection> & { id: string }>) => {
      const { id, ...updates } = action.payload
      if (state.byId[id]) {
        state.byId[id] = { ...state.byId[id], ...updates }
      }
    },
    removeConnection: (state, action: PayloadAction<string>) => {
      delete state.byId[action.payload]
      if (state.activeId === action.payload) {
        const remaining = Object.keys(state.byId)
        state.activeId = remaining.length > 0 ? remaining[0] : null
      }
    },
  },
})

export const { setConnections, setActiveConnection, addConnection, updateConnection, removeConnection } = connectionsSlice.actions

// RTK Query API for connections
export const connectionsApi = createApi({
  reducerPath: 'connectionsApi',
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
  tagTypes: ['Connections'],
  endpoints: (builder) => ({
    getConnections: builder.query<Connection[], void>({
      query: () => '/connections',
      providesTags: ['Connections'],
    }),
    startGmailConnection: builder.mutation<{ consent_url: string; state: string }, void>({
      query: () => ({ url: '/connections/gmail/start', method: 'POST' }),
    }),
    enableWriteAccess: builder.mutation<{ consent_url: string; state: string }, string>({
      query: (connectionId) => ({ url: '/connections/gmail/enable-write', method: 'POST', body: { connection_id: connectionId } }),
    }),
    deleteConnection: builder.mutation<void, string>({
      query: (connectionId) => ({ url: `/connections/${connectionId}`, method: 'DELETE' }),
      invalidatesTags: ['Connections'],
    }),
  }),
})

export const {
  useGetConnectionsQuery,
  useStartGmailConnectionMutation,
  useEnableWriteAccessMutation,
  useDeleteConnectionMutation,
} = connectionsApi

export default connectionsSlice.reducer