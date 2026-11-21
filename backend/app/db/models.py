import enum
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SessionStatus(enum.Enum):
    ACTIVE = 'active'
    REVOKED = 'revoked'
    EXPIRED = 'expired'


class ConnectionStatus(enum.Enum):
    READ_ONLY = 'read_only'
    WRITE_ENABLED = 'write_enabled'
    REVOKED = 'revoked'
    CONNECTING = 'connecting'


class ProposalStatus(enum.Enum):
    PENDING = 'pending'
    APPROVED = 'approved'
    EXECUTING = 'executing'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'


class ExecutionState(enum.Enum):
    PENDING = 'pending'
    SUBMITTED = 'submitted'
    VERIFIED = 'verified'
    FAILED = 'failed'
    INDETERMINATE = 'indeterminate'
    CANCELLED = 'cancelled'


class RunStatus(enum.Enum):
    RUNNING = 'running'
    INTERRUPTED = 'interrupted'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class ToolType(enum.Enum):
    READ = 'read'
    PROPOSE = 'propose'
    EXECUTE = 'execute'


class User(Base):
    __tablename__ = 'users'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    keycloak_subject: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default='active', nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    sessions: Mapped[list['Session']] = relationship(
        back_populates='user', cascade='all, delete-orphan'
    )
    connections: Mapped[list['EmailConnection']] = relationship(
        back_populates='user', cascade='all, delete-orphan'
    )
    threads: Mapped[list['AgentThread']] = relationship(
        back_populates='user', cascade='all, delete-orphan'
    )


class Session(Base):
    __tablename__ = 'sessions'

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    csrf_secret: Mapped[str] = mapped_column(String(64), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    auth_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), default=SessionStatus.ACTIVE, nullable=False
    )

    user: Mapped['User'] = relationship(back_populates='sessions')


class EmailConnection(Base):
    __tablename__ = 'email_connections'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    provider_account: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(ConnectionStatus), default=ConnectionStatus.READ_ONLY, nullable=False
    )
    token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    token_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped['User'] = relationship(back_populates='connections')
    proposals: Mapped[list['ApprovalProposal']] = relationship(
        back_populates='connection', cascade='all, delete-orphan'
    )
    executions: Mapped[list['OperationExecution']] = relationship(
        back_populates='connection', cascade='all, delete-orphan'
    )

    __table_args__ = (
        UniqueConstraint('user_id', 'provider_account', name='uq_user_provider_account'),
        Index('ix_email_connections_user_status', 'user_id', 'status'),
    )


class ConnectionAttempt(Base):
    __tablename__ = 'connection_attempts'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_scopes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class OIDCLoginAttempt(Base):
    __tablename__ = 'oidc_login_attempts'

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_nonce_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class AgentThread(Base):
    __tablename__ = 'agent_threads'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    user: Mapped['User'] = relationship(back_populates='threads')
    runs: Mapped[list['AgentRun']] = relationship(
        back_populates='thread', cascade='all, delete-orphan'
    )


class AgentRun(Base):
    __tablename__ = 'agent_runs'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey('agent_threads.id', ondelete='CASCADE'), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), default=RunStatus.RUNNING, nullable=False
    )
    outcome_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    outcome_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    thread: Mapped['AgentThread'] = relationship(back_populates='runs')
    events: Mapped[list['AgentEvent']] = relationship(
        back_populates='run', cascade='all, delete-orphan', order_by='AgentEvent.seq'
    )


class AgentEvent(Base):
    __tablename__ = 'agent_events'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    run: Mapped['AgentRun'] = relationship(back_populates='events')

    __table_args__ = (
        UniqueConstraint('run_id', 'seq', name='uq_run_seq'),
        Index('ix_agent_events_run_seq', 'run_id', 'seq'),
    )


class ApprovalProposal(Base):
    __tablename__ = 'approval_proposals'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey('email_connections.id', ondelete='CASCADE'), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('agent_runs.id', ondelete='SET NULL'), nullable=True
    )
    tool: Mapped[str] = mapped_column(String(50), nullable=False)
    action_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    candidate_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label_delta_add: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    label_delta_remove: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    estimated_count: Mapped[int] = mapped_column(nullable=False)
    estimated_bytes: Mapped[int] = mapped_column(nullable=False)
    sample_refs: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus), default=ProposalStatus.PENDING, nullable=False
    )
    execution_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    connection: Mapped['EmailConnection'] = relationship(back_populates='proposals')
    items: Mapped[list['ProposalItem']] = relationship(
        back_populates='proposal', cascade='all, delete-orphan', order_by='ProposalItem.message_id'
    )
    decisions: Mapped[list['ApprovalDecision']] = relationship(
        back_populates='proposal', cascade='all, delete-orphan'
    )

    __table_args__ = (
        Index('ix_approval_proposals_user_status', 'user_id', 'status'),
        Index('ix_approval_proposals_connection_status', 'connection_id', 'status'),
    )


class ProposalItem(Base):
    __tablename__ = 'proposal_items'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey('approval_proposals.id', ondelete='CASCADE'), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    prior_label_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    size_estimate: Mapped[int] = mapped_column(default=0, nullable=False)

    proposal: Mapped['ApprovalProposal'] = relationship(back_populates='items')

    __table_args__ = (
        UniqueConstraint('proposal_id', 'message_id', name='uq_proposal_message'),
        Index('ix_proposal_items_proposal_message', 'proposal_id', 'message_id'),
    )


class ApprovalDecision(Base):
    __tablename__ = 'approval_decisions'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey('approval_proposals.id', ondelete='CASCADE'), nullable=False, index=True
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    interrupt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    proposal: Mapped['ApprovalProposal'] = relationship(back_populates='decisions')


class OperationExecution(Base):
    __tablename__ = 'operation_executions'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey('email_connections.id', ondelete='CASCADE'), nullable=False
    )
    action_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[ExecutionState] = mapped_column(
        Enum(ExecutionState), default=ExecutionState.PENDING, nullable=False
    )
    provider_receipt: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    connection: Mapped['EmailConnection'] = relationship(back_populates='executions')
    items: Mapped[list['OperationItem']] = relationship(
        back_populates='execution', cascade='all, delete-orphan'
    )

    __table_args__ = (
        UniqueConstraint(
            'connection_id', 'action_digest', 'chunk_index', name='uq_execution_ledger'
        ),
        Index('ix_operation_executions_connection_digest', 'connection_id', 'action_digest'),
    )


class OperationItem(Base):
    __tablename__ = 'operation_items'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey('operation_executions.id', ondelete='CASCADE'), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    prior_label_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    intended_add_labels: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    intended_remove_labels: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    verified_add_labels: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    verified_remove_labels: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    verification_state: Mapped[str | None] = mapped_column(String(50), nullable=True)

    execution: Mapped['OperationExecution'] = relationship(back_populates='items')


class AuditEvent(Base):
    __tablename__ = 'audit_events'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('email_connections.id', ondelete='SET NULL'), nullable=True, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('agent_runs.id', ondelete='SET NULL'), nullable=True
    )
    proposal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('approval_proposals.id', ondelete='SET NULL'), nullable=True
    )
    execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('operation_executions.id', ondelete='SET NULL'), nullable=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    tool: Mapped[str | None] = mapped_column(String(50), nullable=True)
    policy_decision: Mapped[str] = mapped_column(String(20), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    impact_count: Mapped[int] = mapped_column(default=0, nullable=False)
    impact_bytes: Mapped[int] = mapped_column(default=0, nullable=False)
    label_delta_add: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    label_delta_remove: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    verification_outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    execution_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        Index('ix_audit_events_user_created', 'user_id', 'created_at'),
        Index('ix_audit_events_connection_created', 'connection_id', 'created_at'),
    )
