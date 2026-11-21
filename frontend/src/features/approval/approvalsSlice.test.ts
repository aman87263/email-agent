import { describe, expect, it } from 'vitest'

import reducer, { clearActiveProposal, removeProposal, setProposal } from './approvalsSlice'

const proposal = {
  proposalId: 'p1',
  tool: 'propose_trash',
  estimatedCount: 12,
  estimatedBytes: 4096,
  sampleRefs: [
    { message_id: 'm1', thread_id: 't1', size_estimate: 100 },
  ],
  expiresAt: '2026-11-22T00:00:00Z',
  actionDigest: 'abc123',
  addLabels: ['TRASH'],
  removeLabels: [],
}

describe('approvalsSlice', () => {
  it('starts empty', () => {
    const state = reducer(undefined, { type: 'unknown' })
    expect(state.activeProposalId).toBeNull()
    expect(state.byId).toEqual({})
  })

  it('stores a proposal and makes it active', () => {
    const state = reducer(undefined, setProposal(proposal))
    expect(state.byId.p1).toEqual(proposal)
    expect(state.activeProposalId).toBe('p1')
  })

  it('clears the active proposal without deleting it', () => {
    let state = reducer(undefined, setProposal(proposal))
    state = reducer(state, clearActiveProposal())
    expect(state.activeProposalId).toBeNull()
    expect(state.byId.p1).toBeDefined()
  })

  it('removes a proposal entirely', () => {
    let state = reducer(undefined, setProposal(proposal))
    state = reducer(state, removeProposal('p1'))
    expect(state.byId.p1).toBeUndefined()
    expect(state.activeProposalId).toBeNull()
  })

  it('replaces the active proposal when a new one arrives', () => {
    let state = reducer(undefined, setProposal(proposal))
    state = reducer(state, setProposal({ ...proposal, proposalId: 'p2' }))
    expect(state.activeProposalId).toBe('p2')
    expect(Object.keys(state.byId).sort()).toEqual(['p1', 'p2'])
  })
})
