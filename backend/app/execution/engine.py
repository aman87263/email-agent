import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import ConnectionManager
from app.config import get_settings
from app.db.models import (
    ApprovalProposal,
    AuditEvent,
    ConnectionStatus,
    EmailConnection,
    ExecutionState,
    OperationExecution,
    OperationItem,
    ProposalItem,
    ProposalStatus,
)
from app.db.session import get_engine
from app.policy.engine import PolicyDecision, PolicyEngine
from app.providers.base import PolicyGrant
from app.providers.gmail import GmailProvider


@dataclass
class ExecutionResult:
    execution_id: UUID
    state: ExecutionState
    verified_count: int
    failed_count: int
    indeterminate_chunks: list[int]
    error: str | None = None


class ExecutionEngine:
    def __init__(
        self,
        connection_manager: ConnectionManager,
        gmail_provider: GmailProvider,
        policy_engine: PolicyEngine,
    ):
        self.settings = get_settings()
        self.connection_manager = connection_manager
        self.gmail_provider = gmail_provider
        self.policy_engine = policy_engine

    async def execute_proposal(
        self,
        db: AsyncSession,
        proposal_id: UUID,
        user_id: UUID,
    ) -> ExecutionResult:
        """Execute an approved proposal with full verification and reconciliation."""
        # Get proposal
        proposal_stmt = select(ApprovalProposal).where(
            ApprovalProposal.id == proposal_id,
            ApprovalProposal.user_id == user_id,
        )
        proposal_result = await db.execute(proposal_stmt)
        proposal = proposal_result.scalar_one_or_none()

        if not proposal:
            return ExecutionResult(
                execution_id=UUID(int=0),
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Proposal not found',
            )

        # Verify proposal status
        if proposal.status != ProposalStatus.APPROVED:
            return ExecutionResult(
                execution_id=UUID(int=0),
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error=f'Proposal not approved (status: {proposal.status.value})',
            )

        # Verify expiry
        if proposal.expires_at < datetime.now(UTC):
            return ExecutionResult(
                execution_id=UUID(int=0),
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Proposal expired',
            )

        # Verify digest matches (recompute from proposal items)
        items_stmt = (
            select(ProposalItem)
            .where(ProposalItem.proposal_id == proposal_id)
            .order_by(ProposalItem.message_id)
        )
        items_result = await db.execute(items_stmt)
        items = list(items_result.scalars().all())

        message_ids = [item.message_id for item in items]
        computed_digest = self._compute_action_digest(
            connection_id=proposal.connection_id,
            tool=proposal.tool,
            add_labels=self._get_add_labels(proposal.tool, proposal),
            remove_labels=self._get_remove_labels(proposal.tool, proposal),
            message_ids=message_ids,
            expires_at=proposal.expires_at,
        )

        if computed_digest != proposal.action_digest:
            return ExecutionResult(
                execution_id=UUID(int=0),
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Action digest mismatch - proposal may have been tampered with',
            )

        if proposal.policy_version != self.policy_engine.policy_version:
            return ExecutionResult(
                execution_id=UUID(int=0),
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Proposal policy version is no longer current',
            )

        connection = await self.connection_manager.get_connection(proposal.connection_id, user_id)
        if connection is None:
            return ExecutionResult(
                execution_id=UUID(int=0),
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Mailbox connection is unavailable',
            )

        authorization = self.policy_engine.authorize_resume(connection, proposal, message_ids)
        grant = authorization.grant
        if authorization.decision is PolicyDecision.DENY or grant is None:
            return ExecutionResult(
                execution_id=UUID(int=0),
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error=authorization.reason,
            )

        # Conditional status transition: approved -> executing
        transition_stmt = (
            update(ApprovalProposal)
            .where(
                ApprovalProposal.id == proposal_id,
                ApprovalProposal.status == ProposalStatus.APPROVED,
            )
            .values(status=ProposalStatus.EXECUTING, execution_started_at=datetime.now(UTC))
            .returning(ApprovalProposal.id)
        )
        transition_result = await db.execute(transition_stmt)
        if not transition_result.scalar_one_or_none():
            # Another process already started execution
            return ExecutionResult(
                execution_id=UUID(int=0),
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Proposal already being executed (concurrent execution prevented)',
            )

        await db.commit()

        # Acquire the per-connection advisory lock on a *dedicated* connection and
        # hold it for the whole execution. A session-level lock taken on the
        # AsyncSession would be released as soon as that session commits and
        # returns its connection to the pool, leaving the lock attached to a
        # connection we no longer control.
        lock_key = self._connection_lock_key(proposal.connection_id)
        engine = get_engine()
        async with engine.connect() as lock_conn:
            await lock_conn.execute(
                text('SELECT pg_advisory_lock(:lock_key)'), {'lock_key': lock_key}
            )
            await lock_conn.commit()
            try:
                return await self._execute_chunks(db, proposal, items, grant, user_id)
            finally:
                await lock_conn.execute(
                    text('SELECT pg_advisory_unlock(:lock_key)'), {'lock_key': lock_key}
                )
                await lock_conn.commit()

    def _connection_lock_key(self, connection_id: UUID) -> int:
        """Generate a stable 64-bit lock key from connection UUID."""
        # Use first 8 bytes of SHA256 as 64-bit integer
        hash_bytes = hashlib.sha256(str(connection_id).encode()).digest()
        return int.from_bytes(hash_bytes[:8], 'big', signed=True)

    def _get_add_labels(self, tool: str, proposal: ApprovalProposal) -> list[str]:
        if tool == 'propose_trash':
            return ['TRASH']
        if tool == 'propose_archive':
            return []
        if tool == 'propose_labels':
            return list(proposal.label_delta_add or [])
        return []

    def _get_remove_labels(self, tool: str, proposal: ApprovalProposal) -> list[str]:
        if tool == 'propose_trash':
            return []
        if tool == 'propose_archive':
            return ['INBOX']
        if tool == 'propose_labels':
            return list(proposal.label_delta_remove or [])
        return []

    async def _revalidate_candidate_set(
        self,
        items: list[ProposalItem],
        connection_id: UUID,
        user_id: UUID,
    ) -> tuple[list[ProposalItem], list[str]]:
        """Re-check every candidate against live Gmail state.

        Returns (valid_items, stale_ids). A candidate is stale when it no longer
        exists or when its current labels no longer match the approved snapshot —
        the approval no longer covers it.
        """
        if not items:
            return items, []

        # Quota budget: metadata = 20 units/message; cap at 5,000 units (~250 msgs)
        max_revalidate = min(len(items), self.settings.gmail_max_messages_per_run)
        valid: list[ProposalItem] = []
        stale: list[str] = []

        for item in items[:max_revalidate]:
            try:
                msg = await self.gmail_provider.get_message(
                    message_id=item.message_id,
                    fmt='metadata',
                    connection_id=connection_id,
                    user_id=user_id,
                )
            except Exception:
                # Message gone or unreadable — the approval no longer covers it
                stale.append(item.message_id)
                continue

            current = set(msg.label_ids)
            prior = set(item.prior_label_ids)
            if current != prior:
                # Labels changed since approval — do not touch
                stale.append(item.message_id)
                continue

            valid.append(item)

        # Anything beyond the quota budget is also unverified → stale.
        stale.extend(item.message_id for item in items[max_revalidate:])

        return valid, stale

    async def _abort_stale(
        self,
        db: AsyncSession,
        proposal: ApprovalProposal,
        stale_ids: list[str],
        user_id: UUID,
    ) -> ExecutionResult:
        """Abort the whole proposal before any write when a target drifted."""
        reason = (
            f'Candidate set drifted for {len(stale_ids)} message(s); ' 'aborted before any writes.'
        )

        await db.execute(
            update(ApprovalProposal)
            .where(ApprovalProposal.id == proposal.id)
            .values(status=ProposalStatus.FAILED)
        )

        db.add(
            AuditEvent(
                user_id=user_id,
                connection_id=proposal.connection_id,
                proposal_id=proposal.id,
                action='execute',
                tool=proposal.tool,
                policy_decision='deny',
                policy_version=self.policy_engine.policy_version,
                impact_count=0,
                impact_bytes=0,
                label_delta_add=list(proposal.label_delta_add or []),
                label_delta_remove=list(proposal.label_delta_remove or []),
                verification_outcome='aborted_stale',
                execution_state=ExecutionState.FAILED.value,
            )
        )
        await db.commit()

        return ExecutionResult(
            execution_id=UUID(int=0),
            state=ExecutionState.FAILED,
            verified_count=0,
            failed_count=len(stale_ids),
            indeterminate_chunks=[],
            error=reason,
        )

    async def _execute_chunks(
        self,
        db: AsyncSession,
        proposal: ApprovalProposal,
        items: list[ProposalItem],
        grant: PolicyGrant,
        user_id: UUID,
    ) -> ExecutionResult:
        add_labels = self._get_add_labels(proposal.tool, proposal)
        remove_labels = self._get_remove_labels(proposal.tool, proposal)

        # Pre-flight revalidation: confirm every candidate still exists and its
        # labels still match the approved snapshot. If ANY drifted, abort the
        # whole proposal before a single write — a partial application of an
        # approved set is not what the user approved.
        items, stale_ids = await self._revalidate_candidate_set(items, grant.connection_id, user_id)
        if stale_ids:
            return await self._abort_stale(db, proposal, stale_ids, user_id)

        chunk_size = self.settings.gmail_chunk_size
        total_items = len(items)
        total_chunks = (total_items + chunk_size - 1) // chunk_size

        verified_count = 0
        failed_count = 0
        indeterminate_chunks = []

        for chunk_index in range(total_chunks):
            start = chunk_index * chunk_size
            end = min(start + chunk_size, total_items)
            chunk_items = items[start:end]
            chunk_message_ids = [item.message_id for item in chunk_items]

            # Atomic chunk claim
            exec_id = await self._claim_chunk(db, grant, chunk_index)
            if not exec_id:
                # Chunk already claimed - check existing state
                stmt = select(OperationExecution).where(
                    OperationExecution.connection_id == grant.connection_id,
                    OperationExecution.action_digest == grant.action_digest,
                    OperationExecution.chunk_index == chunk_index,
                )
                result = await db.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing and existing.state == ExecutionState.VERIFIED:
                    verified_count += len(chunk_items)
                    continue
                if existing and existing.state in (
                    ExecutionState.PENDING,
                    ExecutionState.SUBMITTED,
                    ExecutionState.INDETERMINATE,
                ):
                    # Reconcile
                    reconciled = await self._reconcile_chunk(
                        db, existing, chunk_items, grant, user_id, proposal=proposal
                    )
                    if reconciled == ExecutionState.VERIFIED:
                        verified_count += len(chunk_items)
                    elif reconciled == ExecutionState.FAILED:
                        failed_count += len(chunk_items)
                    else:
                        indeterminate_chunks.append(chunk_index)
                        failed_count += len(chunk_items)
                    continue
                if existing and existing.state in (ExecutionState.FAILED, ExecutionState.CANCELLED):
                    # Retry by claiming
                    exec_id = await self._retry_chunk(db, existing, chunk_index)
                else:
                    indeterminate_chunks.append(chunk_index)
                    failed_count += len(chunk_items)
                    continue

            if not exec_id:
                indeterminate_chunks.append(chunk_index)
                failed_count += len(chunk_items)
                continue

            # Execute chunk
            try:
                # Mark as submitted
                await self._update_execution_state(db, exec_id, ExecutionState.SUBMITTED)

                # Call Gmail API
                change = await self.gmail_provider.batch_modify_labels_with_user(
                    grant=grant,
                    message_ids=chunk_message_ids,
                    add=add_labels,
                    remove=remove_labels,
                    user_id=user_id,
                )

                if not change.success:
                    await self._update_execution_state(
                        db, exec_id, ExecutionState.FAILED, error=change.error
                    )
                    failed_count += len(chunk_items)
                    continue

                # Read-back verification
                verified = await self._verify_chunk(
                    db,
                    exec_id,
                    chunk_items,
                    add_labels,
                    remove_labels,
                    grant.connection_id,
                    user_id,
                )

                if verified:
                    await self._update_execution_state(db, exec_id, ExecutionState.VERIFIED)
                    verified_count += len(chunk_items)
                else:
                    await self._update_execution_state(db, exec_id, ExecutionState.INDETERMINATE)
                    indeterminate_chunks.append(chunk_index)
                    failed_count += len(chunk_items)

            except Exception as e:
                # Ambiguous: the provider may or may not have applied the change.
                # Never mark this FAILED (which is retryable) — mark it
                # INDETERMINATE so the read-back reconciler decides.
                await self._update_execution_state(
                    db, exec_id, ExecutionState.INDETERMINATE, error=str(e)
                )
                indeterminate_chunks.append(chunk_index)
                failed_count += len(chunk_items)

        # Determine final state. Indeterminacy dominates: an ambiguous chunk must
        # never be reported as a clean FAILED (which would invite a blind retry).
        if indeterminate_chunks:
            final_state = ExecutionState.INDETERMINATE
        elif failed_count > 0 and verified_count == 0:
            final_state = ExecutionState.FAILED
        else:
            final_state = ExecutionState.VERIFIED

        # Update proposal status. An indeterminate proposal returns to APPROVED so
        # the reconciler can resolve it; re-execution always reads back first.
        proposal_status = {
            ExecutionState.VERIFIED: ProposalStatus.COMPLETED,
            ExecutionState.FAILED: ProposalStatus.FAILED,
            ExecutionState.INDETERMINATE: ProposalStatus.APPROVED,
        }[final_state]

        await db.execute(
            update(ApprovalProposal)
            .where(ApprovalProposal.id == proposal.id)
            .values(status=proposal_status)
        )

        # Write audit event
        await self._write_audit_event(
            db, proposal, grant, verified_count, failed_count, final_state
        )

        await db.commit()

        return ExecutionResult(
            execution_id=grant.connection_id,  # Using connection_id as proxy
            state=final_state,
            verified_count=verified_count,
            failed_count=failed_count,
            indeterminate_chunks=indeterminate_chunks,
        )

    async def _claim_chunk(
        self, db: AsyncSession, grant: PolicyGrant, chunk_index: int
    ) -> UUID | None:
        """Atomically claim a chunk for execution. Returns execution_id if claimed."""
        stmt = (
            insert(OperationExecution)
            .values(
                connection_id=grant.connection_id,
                action_digest=grant.action_digest,
                chunk_index=chunk_index,
                state=ExecutionState.PENDING,
                attempts=0,
            )
            .on_conflict_do_nothing(
                index_elements=['connection_id', 'action_digest', 'chunk_index']
            )
            .returning(OperationExecution.id)
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.scalar_one_or_none()

    async def _retry_chunk(
        self, db: AsyncSession, existing: OperationExecution, chunk_index: int
    ) -> UUID | None:
        """Retry a failed/cancelled chunk by conditionally updating."""
        stmt = (
            update(OperationExecution)
            .where(
                OperationExecution.id == existing.id,
                OperationExecution.state.in_([ExecutionState.FAILED, ExecutionState.CANCELLED]),
            )
            .values(
                state=ExecutionState.PENDING,
                attempts=OperationExecution.attempts + 1,
                error_message=None,
            )
            .returning(OperationExecution.id)
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.scalar_one_or_none()

    async def _update_execution_state(
        self,
        db: AsyncSession,
        execution_id: UUID,
        state: ExecutionState,
        provider_receipt: dict | None = None,
        error: str | None = None,
    ) -> None:
        stmt = (
            update(OperationExecution)
            .where(OperationExecution.id == execution_id)
            .values(
                state=state,
                provider_receipt=provider_receipt,
                error_message=error,
                updated_at=datetime.now(UTC),
            )
        )
        await db.execute(stmt)
        await db.commit()

    async def _verify_chunk(
        self,
        db: AsyncSession,
        execution_id: UUID,
        chunk_items: list[ProposalItem],
        add_labels: list[str],
        remove_labels: list[str],
        connection_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Verify chunk by reading back message labels from Gmail."""
        try:
            # Sample verification - check a subset (not all due to quota)
            # For now, verify all in chunk (chunk size is small enough)
            for item in chunk_items:
                msg = await self.gmail_provider.get_message(
                    message_id=item.message_id,
                    fmt='metadata',
                    connection_id=connection_id,
                    user_id=user_id,
                )

                current_labels = set(msg.label_ids)
                prior_labels = set(item.prior_label_ids)
                expected_labels = (prior_labels - set(remove_labels)) | set(add_labels)

                if current_labels != expected_labels:
                    # Record per-item verification result
                    await self._record_operation_item(
                        db,
                        execution_id,
                        item,
                        add_labels,
                        remove_labels,
                        verified_add=list(
                            add_labels if set(add_labels).issubset(current_labels) else []
                        ),
                        verified_remove=list(
                            remove_labels if set(remove_labels).isdisjoint(current_labels) else []
                        ),
                        verification_state='mismatch',
                    )
                    return False

                await self._record_operation_item(
                    db,
                    execution_id,
                    item,
                    add_labels,
                    remove_labels,
                    verified_add=add_labels,
                    verified_remove=remove_labels,
                    verification_state='verified',
                )

            return True

        except Exception:
            return False

    async def _record_operation_item(
        self,
        db: AsyncSession,
        execution_id: UUID,
        proposal_item: ProposalItem,
        intended_add: list[str],
        intended_remove: list[str],
        verified_add: list[str],
        verified_remove: list[str],
        verification_state: str,
    ) -> None:
        item = OperationItem(
            execution_id=execution_id,
            message_id=proposal_item.message_id,
            prior_label_ids=proposal_item.prior_label_ids,
            intended_add_labels=intended_add,
            intended_remove_labels=intended_remove,
            verified_add_labels=verified_add,
            verified_remove_labels=verified_remove,
            verification_state=verification_state,
        )
        db.add(item)
        await db.commit()

    async def _reconcile_chunk(
        self,
        db: AsyncSession,
        execution: OperationExecution,
        chunk_items: list[ProposalItem],
        grant: PolicyGrant,
        user_id: UUID,
        proposal: ApprovalProposal | None = None,
    ) -> ExecutionState:
        """Reconcile a chunk with unknown state by reading back from Gmail."""
        add_labels = self._get_add_labels(proposal.tool, proposal) if proposal else []
        remove_labels = self._get_remove_labels(proposal.tool, proposal) if proposal else []
        # Get connection to verify
        conn = await self.connection_manager.get_connection(execution.connection_id, user_id)
        if not conn or conn.status == ConnectionStatus.REVOKED:
            return ExecutionState.INDETERMINATE

        # Read back all messages in chunk
        all_match_intended = True
        all_match_prior = True

        for item in chunk_items:
            try:
                msg = await self.gmail_provider.get_message(
                    message_id=item.message_id,
                    fmt='metadata',
                    connection_id=execution.connection_id,
                    user_id=user_id,
                )

                current_labels = set(msg.label_ids)
                prior_labels = set(item.prior_label_ids)
                expected_labels = (prior_labels - set(remove_labels)) | set(add_labels)

                if current_labels == expected_labels:
                    all_match_prior = False
                elif current_labels == prior_labels:
                    all_match_intended = False
                else:
                    # Mixed state
                    all_match_intended = False
                    all_match_prior = False

            except Exception:
                return ExecutionState.INDETERMINATE

        if all_match_intended:
            await self._update_execution_state(db, execution.id, ExecutionState.VERIFIED)
            return ExecutionState.VERIFIED
        if all_match_prior:
            await self._update_execution_state(db, execution.id, ExecutionState.FAILED)
            return ExecutionState.FAILED
        await self._update_execution_state(db, execution.id, ExecutionState.INDETERMINATE)
        return ExecutionState.INDETERMINATE

    def _get_add_labels_from_execution(self, execution: OperationExecution) -> list[str]:
        # Label deltas now live on ApprovalProposal; kept for API compatibility
        return []

    def _get_remove_labels_from_execution(self, execution: OperationExecution) -> list[str]:
        # Label deltas now live on ApprovalProposal; kept for API compatibility
        return []

    def _compute_action_digest(
        self,
        connection_id: UUID,
        tool: str,
        add_labels: list[str],
        remove_labels: list[str],
        message_ids: list[str],
        expires_at: datetime,
    ) -> str:
        payload = {
            'v': 1,
            'policy_version': self.policy_engine.policy_version,
            'connection_id': str(connection_id),
            'tool': tool,
            'add_labels': sorted(add_labels),
            'remove_labels': sorted(remove_labels),
            'candidate_set_hash': hashlib.sha256(''.join(sorted(message_ids)).encode()).hexdigest(),
            'count': len(message_ids),
            'expires_at': expires_at.isoformat(),
        }
        canonical = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def _write_audit_event(
        self,
        db: AsyncSession,
        proposal: ApprovalProposal,
        grant: PolicyGrant,
        verified_count: int,
        failed_count: int,
        final_state: ExecutionState,
    ) -> None:
        # Determine label delta
        add_labels = self._get_add_labels(proposal.tool, proposal)
        remove_labels = self._get_remove_labels(proposal.tool, proposal)

        audit = AuditEvent(
            user_id=proposal.user_id,
            connection_id=proposal.connection_id,
            proposal_id=proposal.id,
            execution_id=None,  # one proposal spans multiple chunk executions
            action='execute',
            tool=proposal.tool,
            policy_decision='allow',
            policy_version=self.policy_engine.policy_version,
            impact_count=verified_count + failed_count,
            impact_bytes=proposal.estimated_bytes,
            label_delta_add=add_labels,
            label_delta_remove=remove_labels,
            verification_outcome=final_state.value,
            execution_state=final_state.value,
        )
        db.add(audit)
        await db.commit()

    async def reconcile_all_pending(self, db: AsyncSession) -> int:
        """Reconcile all pending/submitted executions older than threshold. Called on startup."""

        threshold = datetime.now(UTC) - timedelta(seconds=120)

        executions_stmt = (
            select(OperationExecution, EmailConnection.user_id)
            .join(EmailConnection, EmailConnection.id == OperationExecution.connection_id)
            .where(
                OperationExecution.state.in_([ExecutionState.PENDING, ExecutionState.SUBMITTED]),
                OperationExecution.updated_at < threshold,
            )
        )
        executions_result = await db.execute(executions_stmt)
        executions = list(executions_result.all())

        reconciled = 0
        for execution, user_id in executions:
            # Get proposal to find items
            proposal_stmt = select(ApprovalProposal).where(
                ApprovalProposal.action_digest == execution.action_digest
            )
            proposal_result = await db.execute(proposal_stmt)
            proposal = proposal_result.scalar_one_or_none()

            if not proposal:
                continue

            # Get proposal items for this chunk
            items_stmt = (
                select(ProposalItem)
                .where(ProposalItem.proposal_id == proposal.id)
                .order_by(ProposalItem.message_id)
            )
            items_result = await db.execute(items_stmt)
            all_items = list(items_result.scalars().all())

            chunk_size = self.settings.gmail_chunk_size
            start = execution.chunk_index * chunk_size
            end = min(start + chunk_size, len(all_items))
            chunk_items = all_items[start:end]

            grant = PolicyGrant(
                proposal_id=proposal.id,
                action_digest=execution.action_digest,
                connection_id=execution.connection_id,
                scopes_checked=frozenset(),
                expires_at=datetime.max.replace(tzinfo=UTC),
            )

            await self._reconcile_chunk(
                db, execution, chunk_items, grant, user_id, proposal=proposal
            )
            reconciled += 1

        return reconciled

    async def undo_execution(
        self, db: AsyncSession, execution_id: UUID, user_id: UUID
    ) -> ExecutionResult:
        """Undo one verified chunk after current-state and policy checks."""
        execution_stmt = (
            select(OperationExecution)
            .join(EmailConnection, EmailConnection.id == OperationExecution.connection_id)
            .where(
                OperationExecution.id == execution_id,
                EmailConnection.user_id == user_id,
            )
        )
        execution = (await db.execute(execution_stmt)).scalar_one_or_none()
        if not execution:
            return ExecutionResult(
                execution_id=execution_id,
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Execution not found',
            )

        if execution.state != ExecutionState.VERIFIED:
            return ExecutionResult(
                execution_id=execution_id,
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error=f'Cannot undo chunk in state {execution.state.value}',
            )

        proposal_stmt = select(ApprovalProposal).where(
            ApprovalProposal.connection_id == execution.connection_id,
            ApprovalProposal.action_digest == execution.action_digest,
            ApprovalProposal.user_id == user_id,
        )
        proposal = (await db.execute(proposal_stmt)).scalar_one_or_none()
        if proposal is None or datetime.now(UTC) > execution.created_at + timedelta(days=7):
            return ExecutionResult(
                execution_id=execution_id,
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Undo is unavailable or its seven-day window has expired',
            )

        connection = await self.connection_manager.get_connection(execution.connection_id, user_id)
        if connection is None or connection.status != ConnectionStatus.WRITE_ENABLED:
            return ExecutionResult(
                execution_id=execution_id,
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
                error='Mailbox connection is not write-enabled',
            )

        items_stmt = select(OperationItem).where(OperationItem.execution_id == execution_id)
        items = list((await db.execute(items_stmt)).scalars().all())
        if not items:
            return ExecutionResult(
                execution_id=execution_id,
                state=ExecutionState.VERIFIED,
                verified_count=0,
                failed_count=0,
                indeterminate_chunks=[],
            )

        add_back: set[str] = set()
        remove_now: set[str] = set()
        all_already_undone = True
        for item in items:
            add_back.update(item.verified_remove_labels or [])
            remove_now.update(item.verified_add_labels or [])
            try:
                message = await self.gmail_provider.get_message(
                    message_id=item.message_id,
                    fmt='metadata',
                    connection_id=execution.connection_id,
                    user_id=user_id,
                )
                current_labels = set(message.label_ids)
                prior_labels = set(item.prior_label_ids)
                expected_after_original = (
                    prior_labels - set(item.intended_remove_labels)
                ) | set(item.intended_add_labels)
                if current_labels == prior_labels:
                    continue
                all_already_undone = False
                if current_labels != expected_after_original:
                    return ExecutionResult(
                        execution_id=execution_id,
                        state=ExecutionState.INDETERMINATE,
                        verified_count=0,
                        failed_count=len(items),
                        indeterminate_chunks=[execution.chunk_index],
                        error='Message labels changed since execution; refusing undo',
                    )
            except Exception:
                return ExecutionResult(
                    execution_id=execution_id,
                    state=ExecutionState.INDETERMINATE,
                    verified_count=0,
                    failed_count=len(items),
                    indeterminate_chunks=[execution.chunk_index],
                    error='Unable to verify current labels before undo',
                )

        if all_already_undone:
            return ExecutionResult(
                execution_id=execution_id,
                state=ExecutionState.VERIFIED,
                verified_count=len(items),
                failed_count=0,
                indeterminate_chunks=[],
            )

        authorization = self.policy_engine.authorize_write(
            connection,
            'propose_labels',
            {
                'message_ids': [item.message_id for item in items],
                'add_labels': sorted(add_back),
                'remove_labels': sorted(remove_now),
            },
        )
        grant = authorization.grant
        if authorization.decision is PolicyDecision.DENY or grant is None:
            return ExecutionResult(
                execution_id=execution_id,
                state=ExecutionState.FAILED,
                verified_count=0,
                failed_count=len(items),
                indeterminate_chunks=[],
                error=authorization.reason,
            )
        grant.proposal_id = proposal.id

        try:
            change = await self.gmail_provider.batch_modify_labels_with_user(
                grant=grant,
                message_ids=[item.message_id for item in items],
                add=sorted(add_back),
                remove=sorted(remove_now),
                user_id=user_id,
            )
        except Exception:
            change = None

        # A provider error can be ambiguous, so always read the resulting state.
        verified = True
        for item in items:
            try:
                message = await self.gmail_provider.get_message(
                    message_id=item.message_id,
                    fmt='metadata',
                    connection_id=execution.connection_id,
                    user_id=user_id,
                )
            except Exception:
                verified = False
                break
            if set(message.label_ids) != set(item.prior_label_ids):
                verified = False
                break

        final_state = ExecutionState.VERIFIED if verified else ExecutionState.INDETERMINATE
        db.add(
            AuditEvent(
                user_id=user_id,
                connection_id=execution.connection_id,
                proposal_id=proposal.id,
                execution_id=execution.id,
                action='undo',
                tool=proposal.tool,
                policy_decision=authorization.decision.value,
                policy_version=self.policy_engine.policy_version,
                impact_count=len(items),
                impact_bytes=proposal.estimated_bytes,
                label_delta_add=sorted(add_back),
                label_delta_remove=sorted(remove_now),
                verification_outcome=final_state.value,
                execution_state=final_state.value,
            )
        )
        await db.commit()

        return ExecutionResult(
            execution_id=execution_id,
            state=final_state,
            verified_count=len(items) if verified else 0,
            failed_count=0 if verified else len(items),
            indeterminate_chunks=[] if verified else [execution.chunk_index],
            error=None if verified or change is not None else 'Provider result was ambiguous',
        )
