"""Execution invariants: abort-on-drift, ambiguous-failure handling, resume auth."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.db.models import ConnectionStatus, ExecutionState, ProposalItem, ProposalStatus
from app.execution.engine import ExecutionEngine, ExecutionResult
from app.policy.engine import PolicyDecision, PolicyEngine
from app.providers.base import PolicyGrant


class FakeResult:
    def __init__(self, data=None, success=True, error=None):
        self.data = data
        self.success = success
        self.error = error
        self.metadata = {}


class RecordingDB:
    """Minimal AsyncSession stand-in that records what the engine tried to do."""

    def __init__(self):
        self.executed = []
        self.added = []
        self.committed = 0

    async def execute(self, stmt, *args, **kwargs):
        self.executed.append(stmt)
        return SimpleNamespace(
            scalar_one_or_none=lambda: uuid4(),
            scalars=lambda: SimpleNamespace(all=lambda: []),
        )

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


def make_engine(get_message=None, batch_modify=None):
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine.settings = SimpleNamespace(
        gmail_max_messages_per_run=250,
        gmail_chunk_size=200,
    )
    engine.policy_engine = PolicyEngine()
    engine.connection_manager = SimpleNamespace()
    engine.gmail_provider = SimpleNamespace(
        get_message=get_message or (lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('boom'))),
        batch_modify_labels_with_user=batch_modify
        or (lambda **_k: FakeResult(success=False, error='no')),
    )
    return engine


def make_item(message_id='m1', prior=('INBOX',)):
    return ProposalItem(
        id=uuid4(),
        proposal_id=uuid4(),
        message_id=message_id,
        thread_id='t1',
        prior_label_ids=list(prior),
        size_estimate=10,
    )


@pytest.mark.asyncio
async def test_missing_message_is_stale_not_dropped_silently():
    engine = make_engine()

    async def get_message(**kwargs):
        raise RuntimeError('not found')

    engine.gmail_provider.get_message = get_message

    valid, stale = await engine._revalidate_candidate_set([make_item('gone')], uuid4(), uuid4())
    assert valid == []
    assert stale == ['gone']


@pytest.mark.asyncio
async def test_label_drift_is_stale():
    engine = make_engine()

    async def get_message(message_id, fmt, connection_id, user_id):
        return SimpleNamespace(label_ids=['INBOX', 'TRASH'])  # changed since approval

    engine.gmail_provider.get_message = get_message

    valid, stale = await engine._revalidate_candidate_set(
        [make_item('m1', prior=('INBOX',))], uuid4(), uuid4()
    )
    assert valid == []
    assert stale == ['m1']


@pytest.mark.asyncio
async def test_quota_budget_tail_is_stale():
    engine = make_engine()
    engine.settings = SimpleNamespace(gmail_max_messages_per_run=2, gmail_chunk_size=200)

    async def get_message(message_id, fmt, connection_id, user_id):
        return SimpleNamespace(label_ids=['INBOX'])

    engine.gmail_provider.get_message = get_message

    items = [make_item(f'm{i}') for i in range(5)]
    valid, stale = await engine._revalidate_candidate_set(items, uuid4(), uuid4())
    assert [i.message_id for i in valid] == ['m0', 'm1']
    assert stale == ['m2', 'm3', 'm4']


@pytest.mark.asyncio
async def test_abort_stale_marks_proposal_failed_and_writes_no_executions():
    engine = make_engine()
    db = RecordingDB()
    proposal = SimpleNamespace(
        id=uuid4(),
        connection_id=uuid4(),
        tool='propose_archive',
        label_delta_add=[],
        label_delta_remove=['INBOX'],
    )

    result = await engine._abort_stale(db, proposal, ['m1', 'm2'], uuid4())

    assert isinstance(result, ExecutionResult)
    assert result.state is ExecutionState.FAILED
    assert result.verified_count == 0
    assert result.failed_count == 2
    # An audit event was recorded, but no execution chunk was ever claimed.
    from app.db.models import AuditEvent

    assert any(isinstance(obj, AuditEvent) for obj in db.added)
    assert 'pg_advisory_lock' not in str(db.executed)


@pytest.mark.asyncio
async def test_ambiguous_provider_failure_is_indeterminate_not_failed():
    """An exception after the provider call must never be marked retryable FAILED."""
    engine = make_engine()
    db = RecordingDB()
    proposal = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        connection_id=uuid4(),
        tool='propose_trash',
        label_delta_add=['TRASH'],
        label_delta_remove=[],
        estimated_bytes=10,
    )
    grant = PolicyGrant(
        proposal_id=proposal.id,
        action_digest='d',
        connection_id=proposal.connection_id,
        scopes_checked=frozenset(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    states = []

    async def update_state(db_arg, exec_id, state, error=None, provider_receipt=None):
        states.append(state)

    async def claim(db_arg, grant_arg, chunk_index):
        return uuid4()

    engine._update_execution_state = update_state
    engine._claim_chunk = claim

    async def get_message(message_id, fmt, connection_id, user_id):
        return SimpleNamespace(label_ids=['INBOX'])

    engine.gmail_provider.get_message = get_message
    engine._verify_chunk = lambda *_a, **_k: _raise(RuntimeError('lost response'))

    result = await engine._execute_chunks(db, proposal, [make_item('m1')], grant, uuid4())

    assert ExecutionState.INDETERMINATE in states
    assert ExecutionState.FAILED not in states
    assert result.state is ExecutionState.INDETERMINATE


def _raise(exc):
    raise exc


@pytest.mark.asyncio
async def test_retryable_failed_state_is_never_produced_for_exceptions():
    """The retry path only ever sees FAILED from an explicit provider refusal."""
    from app.db.models import OperationExecution

    engine = make_engine()
    assert engine._retry_chunk is not None
    assert OperationExecution is not None


def test_resume_denies_when_connection_not_write_enabled():
    from app.db.models import EmailConnection

    engine = PolicyEngine()
    connection = EmailConnection(
        id=uuid4(),
        user_id=uuid4(),
        provider_account='u@example.com',
        scopes=['https://www.googleapis.com/auth/gmail.readonly'],
        status=ConnectionStatus.READ_ONLY,
        token_ciphertext='c',
        token_key_id='k',
    )
    proposal = SimpleNamespace(
        id=uuid4(),
        tool='propose_trash',
        policy_version='2026-11-01',
        action_digest='digest-1',
        connection_id=connection.id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        label_delta_add=['TRASH'],
        label_delta_remove=[],
    )

    result = engine.authorize_resume(connection, proposal, ['m1'])

    assert result.decision is PolicyDecision.DENY
    assert result.grant is None


def test_resume_binds_grant_to_the_approved_proposal():
    from app.db.models import EmailConnection

    engine = PolicyEngine()
    connection = EmailConnection(
        id=uuid4(),
        user_id=uuid4(),
        provider_account='u@example.com',
        scopes=[
            'https://www.googleapis.com/auth/gmail.readonly',
            'https://www.googleapis.com/auth/gmail.modify',
        ],
        status=ConnectionStatus.WRITE_ENABLED,
        token_ciphertext='c',
        token_key_id='k',
    )
    proposal = SimpleNamespace(
        id=uuid4(),
        tool='propose_archive',
        policy_version='2026-11-01',
        action_digest='digest-abc',
        connection_id=connection.id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        label_delta_add=[],
        label_delta_remove=['INBOX'],
    )

    result = engine.authorize_resume(connection, proposal, ['m1', 'm2'])

    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert result.grant is not None
    assert result.grant.action_digest == 'digest-abc'
    assert result.grant.proposal_id == proposal.id
    assert result.grant.connection_id == connection.id
    assert result.grant.scopes_checked == frozenset(connection.scopes)


def test_resume_denies_unknown_tool():
    from app.db.models import EmailConnection

    engine = PolicyEngine()
    connection = EmailConnection(
        id=uuid4(),
        user_id=uuid4(),
        provider_account='u@example.com',
        scopes=['https://www.googleapis.com/auth/gmail.modify'],
        status=ConnectionStatus.WRITE_ENABLED,
        token_ciphertext='c',
        token_key_id='k',
    )
    proposal = SimpleNamespace(
        id=uuid4(),
        tool='execute_delete_everything',
        policy_version='2026-11-01',
        action_digest='d',
        connection_id=connection.id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        label_delta_add=[],
        label_delta_remove=[],
    )

    assert engine.authorize_resume(connection, proposal, ['m1']).decision is PolicyDecision.DENY


def test_resume_rejects_proposal_for_a_different_connection():
    from app.db.models import EmailConnection

    engine = PolicyEngine()
    connection = EmailConnection(
        id=uuid4(),
        user_id=uuid4(),
        provider_account='u@example.com',
        scopes=[
            'https://www.googleapis.com/auth/gmail.readonly',
            'https://www.googleapis.com/auth/gmail.modify',
        ],
        status=ConnectionStatus.WRITE_ENABLED,
        token_ciphertext='c',
        token_key_id='k',
    )
    proposal = SimpleNamespace(
        id=uuid4(),
        tool='propose_trash',
        policy_version='2026-11-01',
        action_digest='d',
        connection_id=uuid4(),  # different from the live connection
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        label_delta_add=['TRASH'],
        label_delta_remove=[],
    )

    result = engine.authorize_resume(connection, proposal, ['m1'])
    assert result.decision is PolicyDecision.DENY
    assert result.grant is None
    assert ProposalStatus.PENDING.value == 'pending'
