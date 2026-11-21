import { createSlice, PayloadAction } from '@reduxjs/toolkit'

interface UserProfile {
  id: string
  email: string
  keycloak_subject: string
}

interface AuthState {
  status: 'idle' | 'loading' | 'authenticated' | 'unauthenticated'
  profile: UserProfile | null
  roles: string[]
  csrfToken: string | null
  error: string | null
}

const initialState: AuthState = {
  status: 'idle',
  profile: null,
  roles: [],
  csrfToken: null,
  error: null,
}

const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    setAuthenticated: (
      state,
      action: PayloadAction<{ profile: UserProfile; roles: string[]; csrfToken: string }>
    ) => {
      state.status = 'authenticated'
      state.profile = action.payload.profile
      state.roles = action.payload.roles
      state.csrfToken = action.payload.csrfToken
      state.error = null
    },
    setUnauthenticated: (state) => {
      state.status = 'unauthenticated'
      state.profile = null
      state.roles = []
      state.csrfToken = null
      state.error = null
    },
    setLoading: (state) => {
      state.status = 'loading'
    },
    setError: (state, action: PayloadAction<string>) => {
      state.status = 'unauthenticated'
      state.error = action.payload
    },
    updateCsrfToken: (state, action: PayloadAction<string>) => {
      state.csrfToken = action.payload
    },
  },
})

export const { setAuthenticated, setUnauthenticated, setLoading, setError, updateCsrfToken } = authSlice.actions
export default authSlice.reducer