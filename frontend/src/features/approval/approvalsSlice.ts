import { createSlice, PayloadAction } from '@reduxjs/toolkit'

export interface ProposalSnapshot {
  proposalId: string
  tool: string
  estimatedCount: number
  estimatedBytes: number
  sampleRefs: Array<{
    message_id: string
    thread_id: string
    subject: string
    from: string
    date: string
    size_estimate: number
  }>
  expiresAt: string
  actionDigest: string
  addLabels?: string[]
  removeLabels?: string[]
}

interface ApprovalsState {
  byId: Record<string, ProposalSnapshot>
  activeProposalId: string | null
}

const initialState: ApprovalsState = {
  byId: {},
  activeProposalId: null,
}

const approvalsSlice = createSlice({
  name: 'approvals',
  initialState,
  reducers: {
    setProposal: (state, action: PayloadAction<ProposalSnapshot>) => {
      state.byId[action.payload.proposalId] = action.payload
      state.activeProposalId = action.payload.proposalId
    },
    clearActiveProposal: (state) => {
      state.activeProposalId = null
    },
    removeProposal: (state, action: PayloadAction<string>) => {
      delete state.byId[action.payload]
      if (state.activeProposalId === action.payload) {
        state.activeProposalId = null
      }
    },
  },
})

export const { setProposal, clearActiveProposal, removeProposal } = approvalsSlice.actions
export default approvalsSlice.reducer