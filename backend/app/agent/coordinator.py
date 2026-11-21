import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agui import AGUIEventMapper
from app.auth.session import ConnectionManager
from app.config import get_settings
from app.db.models import (
    AgentEvent,
    AgentRun,
    AgentThread,
    ApprovalProposal,
    ProposalItem,
    ProposalStatus,
    RunStatus,
)
from app.db.session import get_session
from app.policy.engine import PolicyDecision, PolicyEngine
from app.providers.gmail import GmailProvider


class AgentCoordinator:
    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self.settings = get_settings()
        self.policy_engine = PolicyEngine()
        self.connection_manager = ConnectionManager()
        self.gmail_provider = GmailProvider(self.connection_manager)
        self.agui_mapper = AGUIEventMapper()

    async def create_run(
        self,
        thread_id: UUID | None,
        messages: list[dict],
        state: dict | None,
        tools: list[dict] | None,
        context: dict | None,
        resume: list[dict] | None,
    ) -> AgentRun:
        """Create a new agent run (or resume from interrupt)."""
        async with get_session() as db:
            # Get or create thread
            if thread_id:
                stmt = select(AgentThread).where(
                    AgentThread.id == thread_id,
                    AgentThread.user_id == self.user_id,
                )
                result = await db.execute(stmt)
                thread = result.scalar_one_or_none()
                if not thread:
                    raise ValueError('Thread not found')
            else:
                thread = AgentThread(user_id=self.user_id, title='New conversation')
                db.add(thread)
                await db.flush()

            # Create run
            run = AgentRun(
                thread_id=thread.id,
                user_id=self.user_id,
                status=RunStatus.RUNNING,
            )
            db.add(run)
            await db.flush()

            # Record initial event
            event = AgentEvent(
                run_id=run.id,
                seq=1,
                event_type='RunStarted',
                payload={
                    'threadId': str(thread.id),
                    'runId': str(run.id),
                    'messages': messages,
                    'state': state or {},
                    'tools': tools or [],
                    'context': context or {},
                    'resume': resume or [],
                },
            )
            db.add(event)
            await db.commit()
            await db.refresh(run)

            return run

    async def get_run(self, run_id: UUID) -> AgentRun | None:
        async with get_session() as db:
            stmt = select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.user_id == self.user_id,
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def get_events(self, run_id: UUID, after: int | None = None) -> list[dict]:
        async with get_session() as db:
            # Scope to the owner: join the run and filter by user_id
            stmt = (
                select(AgentEvent)
                .join(AgentRun, AgentEvent.run_id == AgentRun.id)
                .where(AgentEvent.run_id == run_id, AgentRun.user_id == self.user_id)
            )
            if after is not None:
                stmt = stmt.where(AgentEvent.seq > after)
            stmt = stmt.order_by(AgentEvent.seq)
            result = await db.execute(stmt)
            events = result.scalars().all()
            return [
                {
                    'seq': e.seq,
                    'event_type': e.event_type,
                    'payload': e.payload,
                }
                for e in events
            ]

    async def cancel_run(self, run_id: UUID) -> None:
        async with get_session() as db:
            stmt = select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.user_id == self.user_id,
            )
            result = await db.execute(stmt)
            run = result.scalar_one_or_none()
            if run and run.status == RunStatus.RUNNING:
                run.status = RunStatus.CANCELLED
                run.completed_at = datetime.now(UTC)
                await db.commit()

    async def execute_run(
        self,
        run_id: UUID,
        resume: list[dict] | None = None,
        messages: list[dict] | None = None,
    ) -> None:
        """Execute the agent run.

        Streams AG-UI lifecycle events, drives the tool executor for new
        messages, and on `resume` executes an approved proposal through the
        server-only execution engine.
        """
        run = await self.get_run(run_id)
        if not run:
            return

        if resume:
            await self._handle_resume(run, resume)
            await self._finish(run, {'type': 'success'})
            return

        outcome = await self._handle_new_message(run, messages or [])
        await self._finish(run, outcome)

    async def _handle_new_message(self, run: AgentRun, messages: list[dict]) -> dict:
        """Dispatch a user message through the read/propose tool executor.

        This is the deterministic stand-in for the LLM loop: the same tool
        registry, the same policy engine, the same proposal path. Only the
        intent parsing is hand-written.
        """
        from app.tools.registry import ToolExecutor

        user_text = ''
        for message in reversed(messages):
            if message.get('role') == 'user':
                content = message.get('content')
                user_text = content if isinstance(content, str) else str(content)
                break

        connection = await self.connection_manager.get_active_connection(self.user_id)
        if connection is None:
            await self.add_event(
                run.id,
                'TEXT_MESSAGE_CONTENT',
                {
                    'messageId': str(uuid4()),
                    'delta': (
                        'Connect a Gmail account first — open Connections and '
                        'complete the Google consent screen.'
                    ),
                },
            )
            return {'type': 'success'}

        executor = ToolExecutor(self)
        query = self._extract_query(user_text)

        # 1. Always search first so every intent operates on real message IDs.
        search = await self._call_tool(
            run, executor, connection.id, 'search_messages', {'query': query}
        )
        message_ids = (search.data or {}).get('message_ids', []) if search.success else []

        if not message_ids:
            await self.add_event(
                run.id,
                'TEXT_MESSAGE_CONTENT',
                {
                    'messageId': str(uuid4()),
                    'delta': f'No messages matched "{query}". Try a different query.',
                },
            )
            return {'type': 'success'}

        # 2. Route to a propose tool when the message asks for a mutation.
        intent = self._classify_intent(user_text)
        if intent:
            tool, extra = intent
            if tool == 'propose_labels':
                extra = await self._resolve_label_ids(extra)
            args = {'message_ids': message_ids, **extra}
            result = await self._call_tool(run, executor, connection.id, tool, args)
            if result.success and result.metadata.get('interrupt'):
                return self._interrupt_outcome(result)
            await self.add_event(
                run.id,
                'TEXT_MESSAGE_CONTENT',
                {
                    'messageId': str(uuid4()),
                    'delta': result.error or 'That action could not be prepared.',
                },
            )
            return {'type': 'success'}

        # 3. Read-only summary: metadata for the top hits, then a digest.
        top = message_ids[:10]
        for message_id in top:
            await self._call_tool(
                run,
                executor,
                connection.id,
                'get_message',
                {'message_id': message_id, 'format': 'metadata'},
            )

        lines = []
        for message_id in top[:5]:
            detail = await executor.execute(
                'get_message',
                {'message_id': message_id, 'format': 'metadata'},
                connection.id,
            )
            if detail.success and detail.data:
                headers = detail.data.get('headers', {})
                lines.append(
                    f"• {headers.get('Subject', '(no subject)')} — "
                    f"{headers.get('From', 'unknown')} "
                    f"({headers.get('Date', '')})"
                )

        await self.add_event(
            run.id,
            'TEXT_MESSAGE_CONTENT',
            {
                'messageId': str(uuid4()),
                'delta': (
                    f'Found {len(message_ids)} message(s) for "{query}".\n'
                    + '\n'.join(lines)
                    + '\n\nAsk me to archive or trash this selection and I will '
                    'ask for your approval first.'
                ),
            },
        )
        return {'type': 'success'}

    async def _resolve_label_ids(self, extra: dict) -> dict:
        """Map a human label name to the mailbox's real label ID."""
        from app.tools.registry import ToolExecutor  # local: avoid a cycle

        names = extra.get('add_labels') or []
        if not names:
            return extra

        connection = await self.connection_manager.get_active_connection(self.user_id)
        if connection is None:
            return extra

        executor = ToolExecutor(self)
        result = await executor.execute('list_labels', {}, connection.id)
        if not result.success or not result.data:
            return extra

        by_name = {
            label.get('name', '').lower(): label.get('id')
            for label in result.data.get('labels', [])
        }
        resolved = [by_name.get(name.lower(), name) for name in names]
        return {**extra, 'add_labels': resolved}

    async def _call_tool(self, run, executor, connection_id, tool, args):
        """Emit AG-UI tool events and return the executor result."""
        call_id = str(uuid4())
        await self.add_event(
            run.id,
            'TOOL_CALL_START',
            {'toolCallId': call_id, 'toolName': tool, 'args': args},
        )
        result = await executor.execute(tool, args, connection_id)
        await self.add_event(
            run.id,
            'TOOL_CALL_RESULT',
            {
                'toolCallId': call_id,
                'result': result.data if result.success else None,
                'error': result.error,
            },
        )
        return result

    def _interrupt_outcome(self, result) -> dict:
        data = result.data or {}
        return {
            'type': 'interrupt',
            'interrupts': [
                {
                    'id': data.get('proposal_id'),
                    'type': 'approval',
                    'tool': data.get('tool'),
                    'title': self._interrupt_title(data.get('tool')),
                    'payload': {
                        'proposalId': data.get('proposal_id'),
                        'tool': data.get('tool'),
                        'messageIds': data.get('message_ids', []),
                        'estimatedCount': data.get('estimated_count', 0),
                        'estimatedBytes': data.get('estimated_bytes', 0),
                        'sampleRefs': data.get('sample_refs', []),
                        'expiresAt': data.get('expires_at'),
                        'actionDigest': data.get('action_digest'),
                        'labelDeltaAdd': data.get('label_delta_add', []),
                        'labelDeltaRemove': data.get('label_delta_remove', []),
                    },
                }
            ],
        }

    @staticmethod
    def _interrupt_title(tool: str | None) -> str:
        return {
            'propose_trash': 'Move to Trash',
            'propose_archive': 'Archive Messages',
            'propose_labels': 'Change Labels',
        }.get(tool or '', 'Approve Action')

    @staticmethod
    def _extract_query(text: str) -> str:
        """Strip the intent verb so the remainder is a usable Gmail query."""
        stripped = text.strip()
        for prefix in (
            'please ',
            'can you ',
            'could you ',
            'hey ',
            'hi ',
            'show me ',
            'find me ',
            'search for ',
            'look for ',
        ):
            if stripped.lower().startswith(prefix):
                stripped = stripped[len(prefix) :]
        for verb in ('archive', 'trash', 'delete', 'label', 'tag', 'find', 'show', 'search'):
            if stripped.lower().startswith(verb + ' '):
                stripped = stripped[len(verb) + 1 :]
                break
        return stripped.strip() or text.strip()

    @staticmethod
    def _classify_intent(text: str) -> tuple[str, dict] | None:
        lowered = text.lower()
        if 'archive' in lowered:
            return 'propose_archive', {}
        if 'trash' in lowered or 'delete' in lowered:
            return 'propose_trash', {}
        # Label changes need an explicit target ("label … as Receipts").
        if 'label' in lowered or 'tag' in lowered:
            match = re.search(r'\bas\s+([A-Za-z0-9 _-]+)', lowered)
            if match:
                return (
                    'propose_labels',
                    {'add_labels': [match.group(1).strip()], 'remove_labels': []},
                )
        return None

    async def _handle_resume(self, run: AgentRun, resume: list[dict]) -> None:
        """Apply an approved (or rejected) interrupt."""
        from app.execution.engine import ExecutionEngine

        for item in resume or []:
            proposal_id = item.get('interruptId') or item.get('proposal_id')
            status = item.get('status')
            if not proposal_id or status not in ('approved', 'rejected'):
                continue

            async with get_session() as db:
                stmt = select(ApprovalProposal).where(ApprovalProposal.id == proposal_id)
                result = await db.execute(stmt)
                proposal = result.scalar_one_or_none()
                if not proposal or proposal.user_id != self.user_id:
                    continue

                if status == 'rejected':
                    await db.execute(
                        update(ApprovalProposal)
                        .where(ApprovalProposal.id == proposal_id)
                        .values(status=ProposalStatus.CANCELLED)
                    )
                    await db.commit()
                    await self.add_event(
                        run.id,
                        'TEXT_MESSAGE_CONTENT',
                        {
                            'messageId': str(uuid4()),
                            'delta': 'Action cancelled. Nothing was changed.',
                        },
                    )
                    continue

                # Re-authorize against live connection state — the stored grant
                # is never trusted on the resume path.
                conn = await self.connection_manager.get_connection(
                    proposal.connection_id, self.user_id
                )
                items = list(
                    (
                        await db.execute(
                            select(ProposalItem).where(ProposalItem.proposal_id == proposal.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                message_ids = [item.message_id for item in items]

                if conn is None:
                    await self.add_event(
                        run.id,
                        'TEXT_MESSAGE_CONTENT',
                        {
                            'messageId': str(uuid4()),
                            'delta': (
                                'That action is no longer authorized — the Gmail '
                                'connection was revoked.'
                            ),
                        },
                    )
                    continue

                policy_result = self.policy_engine.authorize_resume(conn, proposal, message_ids)
                if policy_result.decision is PolicyDecision.DENY or policy_result.grant is None:
                    await self.add_event(
                        run.id,
                        'TEXT_MESSAGE_CONTENT',
                        {
                            'messageId': str(uuid4()),
                            'delta': (
                                'That action is no longer authorized — the Gmail '
                                f'connection changed. ({policy_result.reason})'
                            ),
                        },
                    )
                    continue

                # Approved: conditional transition pending -> approved
                res = await db.execute(
                    update(ApprovalProposal)
                    .where(
                        ApprovalProposal.id == proposal_id,
                        ApprovalProposal.status == ProposalStatus.PENDING,
                    )
                    .values(status=ProposalStatus.APPROVED)
                    .returning(ApprovalProposal.id)
                )
                if not res.scalar_one_or_none():
                    continue
                await db.commit()

                engine = ExecutionEngine(
                    self.connection_manager, self.gmail_provider, self.policy_engine
                )
                exec_result = await engine.execute_proposal(db, proposal.id, self.user_id)

                await self.add_event(
                    run.id,
                    'TEXT_MESSAGE_CONTENT',
                    {
                        'messageId': str(uuid4()),
                        'delta': (
                            f'Executed on {exec_result.verified_count} message(s); '
                            f'{exec_result.failed_count} failed/indeterminate.'
                        ),
                    },
                )

    async def _finish(self, run: AgentRun, outcome: dict) -> None:
        async with get_session() as db:
            stmt = select(AgentRun).where(AgentRun.id == run.id)
            result = await db.execute(stmt)
            current = result.scalar_one_or_none()
            if not current:
                return

            next_seq = await self._get_next_seq(db, run.id)
            db.add(
                AgentEvent(
                    run_id=run.id,
                    seq=next_seq,
                    event_type='RunFinished',
                    payload={'outcome': outcome},
                )
            )
            current.status = RunStatus.COMPLETED
            current.outcome_type = outcome.get('type')
            current.outcome_payload = outcome
            current.completed_at = datetime.now(UTC)
            await db.commit()

    async def current_seq(self, run_id: UUID) -> int:
        async with get_session() as db:
            return await self._get_next_seq(db, run_id) - 1

    async def _get_next_seq(self, db: AsyncSession, run_id: UUID) -> int:
        stmt = (
            select(AgentEvent.seq)
            .where(AgentEvent.run_id == run_id)
            .order_by(AgentEvent.seq.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        last_seq = result.scalar_one_or_none()
        return (last_seq or 0) + 1

    async def add_event(self, run_id: UUID, event_type: str, payload: dict) -> int:
        """Add an event to the run."""
        async with get_session() as db:
            next_seq = await self._get_next_seq(db, run_id)
            event = AgentEvent(run_id=run_id, seq=next_seq, event_type=event_type, payload=payload)
            db.add(event)
            await db.commit()
            return next_seq
