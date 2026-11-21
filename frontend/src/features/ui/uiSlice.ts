import { createSlice, PayloadAction } from '@reduxjs/toolkit'

interface UIState {
  selectedThreadId: string | null
  sidebarOpen: boolean
  theme: 'light' | 'dark' | 'system'
  preferences: {
    compactMode: boolean
    showImages: boolean
    autoLoadImages: boolean
  }
}

const initialState: UIState = {
  selectedThreadId: null,
  sidebarOpen: true,
  theme: 'system',
  preferences: {
    compactMode: false,
    showImages: true,
    autoLoadImages: false,
  },
}

const uiSlice = createSlice({
  name: 'ui',
  initialState,
  reducers: {
    setSelectedThread: (state, action: PayloadAction<string | null>) => {
      state.selectedThreadId = action.payload
    },
    toggleSidebar: (state) => {
      state.sidebarOpen = !state.sidebarOpen
    },
    setSidebarOpen: (state, action: PayloadAction<boolean>) => {
      state.sidebarOpen = action.payload
    },
    setTheme: (state, action: PayloadAction<'light' | 'dark' | 'system'>) => {
      state.theme = action.payload
    },
    updatePreferences: (state, action: PayloadAction<Partial<UIState['preferences']>>) => {
      state.preferences = { ...state.preferences, ...action.payload }
    },
  },
})

export const { setSelectedThread, toggleSidebar, setSidebarOpen, setTheme, updatePreferences } = uiSlice.actions
export default uiSlice.reducer