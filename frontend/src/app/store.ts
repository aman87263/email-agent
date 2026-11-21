import { configureStore, combineReducers } from '@reduxjs/toolkit'
import { persistStore, persistReducer } from 'redux-persist'
import storage from 'redux-persist/lib/storage'
import authSlice from '../features/auth/authSlice'
import connectionsSlice from '../features/connections/connectionsSlice'
import runsSlice from '../features/agent/runsSlice'
import approvalsSlice from '../features/approval/approvalsSlice'
import auditSlice from '../features/audit/auditSlice'
import uiSlice from '../features/ui/uiSlice'

const persistConfig = {
  key: 'email-agent',
  version: 1,
  storage,
  whitelist: ['ui'], // Only persist UI preferences
}

const rootReducer = combineReducers({
  auth: authSlice,
  connections: connectionsSlice,
  runs: runsSlice,
  approvals: approvalsSlice,
  audit: auditSlice,
  ui: uiSlice,
})

const persistedReducer = persistReducer(persistConfig, rootReducer)

export const store = configureStore({
  reducer: persistedReducer,
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({
      serializableCheck: {
        ignoredActions: ['persist/PERSIST', 'persist/REHYDRATE'],
      },
    }),
})

export const persistor = persistStore(store)

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch