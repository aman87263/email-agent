import hashlib
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.agent.coordinator import AgentCoordinator
from app.auth.session import ConnectionManager
from app.config import get_settings
from app.policy.engine import PolicyEngine
from app.providers.gmail import GmailProvider


class ToolResult(BaseModel):
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


class SearchMessagesArgs(BaseModel):
    query: str
    max_results: int = Field(default=100, le=500)


class GetMessageArgs(BaseModel):
    message_id: str
    format: str = Field(default='metadata', pattern='^(metadata|full|minimal|raw)$')


class GetThreadArgs(BaseModel):
    thread_id: str
    format: str = Field(default='metadata', pattern='^(metadata|full|minimal|raw)$')


class ListLabelsArgs(BaseModel):
    pass


class SummarizeSelectionArgs(BaseModel):
    message_ids: list[str]
    instruction: str | None = None


class GroupBySenderArgs(BaseModel):
    message_ids: list[str]


class ProposeLabelsArgs(BaseModel):
    message_ids: list[str]
    add_labels: list[str] = Field(default_factory=list)
    remove_labels: list[str] = Field(default_factory=list)


class ProposeArchiveArgs(BaseModel):
    message_ids: list[str]


class ProposeTrashArgs(BaseModel):
    message_ids: list[str]


# Tool registry - model-visible tools only
TOOL_REGISTRY = {
    'search_messages': {
        'name': 'search_messages',
        'description': (
            'Search Gmail messages using Gmail query syntax. '
            'Returns message IDs and metadata for two-stage retrieval.'
        ),
        'parameters': SearchMessagesArgs.model_json_schema(),
        'type': 'read',
    },
    'get_message': {
        'name': 'get_message',
        'description': (
            'Get full message details including headers, body, and labels. '
            'Use format=metadata for headers only (cheaper).'
        ),
        'parameters': GetMessageArgs.model_json_schema(),
        'type': 'read',
    },
    'get_thread': {
        'name': 'get_thread',
        'description': 'Get all messages in a thread.',
        'parameters': GetThreadArgs.model_json_schema(),
        'type': 'read',
    },
    'list_labels': {
        'name': 'list_labels',
        'description': 'List all Gmail labels for the connected account.',
        'parameters': ListLabelsArgs.model_json_schema(),
        'type': 'read',
    },
    'summarize_selection': {
        'name': 'summarize_selection',
        'description': (
            'Summarize a set of already-fetched messages. '
            'Operates locally on provided message IDs.'
        ),
        'parameters': SummarizeSelectionArgs.model_json_schema(),
        'type': 'derived',
    },
    'group_by_sender': {
        'name': 'group_by_sender',
        'description': (
            'Group messages by sender for analysis. ' 'Operates locally on provided message IDs.'
        ),
        'parameters': GroupBySenderArgs.model_json_schema(),
        'type': 'derived',
    },
    'propose_labels': {
        'name': 'propose_labels',
        'description': (
            'Propose label changes (add/remove) on messages. '
            'Requires human approval before execution.'
        ),
        'parameters': ProposeLabelsArgs.model_json_schema(),
        'type': 'propose',
    },
    'propose_archive': {
        'name': 'propose_archive',
        'description': (
            'Propose archiving messages (removing INBOX label). '
            'Requires human approval before execution.'
        ),
        'parameters': ProposeArchiveArgs.model_json_schema(),
        'type': 'propose',
    },
    'propose_trash': {
        'name': 'propose_trash',
        'description': (
            'Propose moving messages to Trash. Requires human approval. '
            'Does not free storage until Trash is emptied.'
        ),
        'parameters': ProposeTrashArgs.model_json_schema(),
        'type': 'propose',
    },
}


class ToolExecutor:
    """Executes read and propose tools. Write tools (execute_*) are server-only."""

    def __init__(self, coordinator: AgentCoordinator):
        self.coordinator = coordinator
        self.settings = get_settings()
        self.policy_engine = PolicyEngine()
        self.connection_manager = ConnectionManager()
        self.gmail_provider = GmailProvider(self.connection_manager)

    async def execute(self, tool_name: str, args: dict, connection_id: UUID) -> ToolResult:
        """Execute a tool by name."""
        # Get active connection
        conn = await self.connection_manager.get_connection(connection_id, self.coordinator.user_id)
        if not conn:
            return ToolResult(success=False, error='No active Gmail connection')

        # Authorize
        policy_result = self.policy_engine.authorize(conn, tool_name, args)
        if policy_result.decision.value == 'deny':
            return ToolResult(success=False, error=policy_result.reason)

        # Route to appropriate handler
        if tool_name == 'search_messages':
            return await self._search_messages(args, conn)
        if tool_name == 'get_message':
            return await self._get_message(args, conn)
        if tool_name == 'get_thread':
            return await self._get_thread(args, conn)
        if tool_name == 'list_labels':
            return await self._list_labels(conn)
        if tool_name == 'summarize_selection':
            return await self._summarize_selection(args)
        if tool_name == 'group_by_sender':
            return await self._group_by_sender(args)
        if tool_name in ('propose_labels', 'propose_archive', 'propose_trash'):
            return await self._create_proposal(tool_name, args, conn, policy_result)
        return ToolResult(success=False, error=f'Unknown tool: {tool_name}')

    async def _search_messages(self, args: dict, conn) -> ToolResult:
        from app.providers.base import MessageQuery

        query = MessageQuery(query=args['query'], max_results=args.get('max_results', 100))
        result = await self.gmail_provider.search_messages(query, conn.id, self.coordinator.user_id)

        # Two-stage retrieval: return IDs and metadata count
        # The model should then call get_message with format=metadata for headers
        return ToolResult(
            success=True,
            data={
                'message_ids': result.message_ids,
                'next_page_token': result.next_page_token,
                'result_size_estimate': result.result_size_estimate,
                'message': (
                    f'Found {len(result.message_ids)} messages '
                    f'(estimated {result.result_size_estimate} total). '
                    'Use get_message with format=metadata for headers.'
                ),
            },
            metadata={'quota_units': len(result.message_ids) * 5},
        )

    async def _get_message(self, args: dict, conn) -> ToolResult:
        fmt = args.get('format', 'metadata')
        msg = await self.gmail_provider.get_message(
            args['message_id'], fmt, conn.id, self.coordinator.user_id
        )

        quota_units = 60 if fmt == 'full' else 20
        return ToolResult(
            success=True,
            data={
                'id': msg.id,
                'thread_id': msg.thread_id,
                'label_ids': msg.label_ids,
                'size_estimate': msg.size_estimate,
                'headers': msg.headers,
                'snippet': msg.snippet,
                'internal_date': msg.internal_date.isoformat() if msg.internal_date else None,
                'body_text': msg.body_text,
                'body_html': msg.body_html,
            },
            metadata={'quota_units': quota_units},
        )

    async def _get_thread(self, args: dict, conn) -> ToolResult:
        fmt = args.get('format', 'metadata')
        thread = await self.gmail_provider.get_thread(
            args['thread_id'], fmt, conn.id, self.coordinator.user_id
        )

        return ToolResult(
            success=True,
            data={
                'id': thread.id,
                'messages': [
                    {
                        'id': m.id,
                        'thread_id': m.thread_id,
                        'label_ids': m.label_ids,
                        'size_estimate': m.size_estimate,
                        'headers': m.headers,
                        'snippet': m.snippet,
                        'body_text': m.body_text,
                        'body_html': m.body_html,
                    }
                    for m in thread.messages
                ],
            },
            metadata={'quota_units': len(thread.messages) * (60 if fmt == 'full' else 20)},
        )

    async def _list_labels(self, conn) -> ToolResult:
        labels = await self.gmail_provider.list_labels(conn.id, self.coordinator.user_id)
        return ToolResult(
            success=True,
            data={
                'labels': [
                    {
                        'id': label.id,
                        'name': label.name,
                        'type': label.type,
                        'messages_total': label.messages_total,
                        'messages_unread': label.messages_unread,
                    }
                    for label in labels
                ]
            },
        )

    async def _summarize_selection(self, args: dict) -> ToolResult:
        # This is a derived tool - operates on already-fetched data
        # In practice, the model would have the message data in context
        # For now, return a placeholder
        return ToolResult(
            success=True,
            data={
                'summary': (
                    'SummarizeSelection is a derived tool - implement with '
                    'local LLM call on fetched messages'
                ),
                'message_ids': args['message_ids'],
            },
        )

    async def _group_by_sender(self, args: dict) -> ToolResult:
        return ToolResult(
            success=True,
            data={
                'groups': (
                    'GroupBySender is a derived tool - implement with '
                    'local processing on fetched messages'
                ),
                'message_ids': args['message_ids'],
            },
        )

    async def _create_proposal(self, tool_name: str, args: dict, conn, policy_result) -> ToolResult:
        # Create proposal in database
        from app.db.models import ApprovalProposal, ProposalItem, ProposalStatus
        from app.db.session import get_session

        message_ids = args.get('message_ids', [])

        # Get message details for proposal items (metadata for prior labels and size)
        proposal_items = []
        sample_refs = []
        total_bytes = 0

        for i, msg_id in enumerate(message_ids):
            msg = await self.gmail_provider.get_message(
                msg_id, 'metadata', conn.id, self.coordinator.user_id
            )
            proposal_items.append(
                {
                    'message_id': msg.id,
                    'thread_id': msg.thread_id,
                    'prior_label_ids': msg.label_ids,
                    'size_estimate': msg.size_estimate,
                }
            )
            total_bytes += msg.size_estimate

            # Proposal persistence is intentionally body-free.  These durable
            # references are sufficient to identify the approved targets; never
            # put subject, address, date, snippet, or body data in the database.
            if i < 5:
                sample_refs.append(
                    {
                        'message_id': msg.id,
                        'thread_id': msg.thread_id,
                        'size_estimate': msg.size_estimate,
                    }
                )

        # Determine label changes
        if tool_name == 'propose_trash':
            add_labels = ['TRASH']
            remove_labels = []
        elif tool_name == 'propose_archive':
            add_labels = []
            remove_labels = ['INBOX']
        elif tool_name == 'propose_labels':
            add_labels = args.get('add_labels', [])
            remove_labels = args.get('remove_labels', [])
        else:
            add_labels = []
            remove_labels = []

        # Create proposal
        async with get_session() as db:
            # Candidate set hash: SHA-256 over the sorted message IDs
            candidate_set_hash = hashlib.sha256(''.join(sorted(message_ids)).encode()).hexdigest()

            proposal = ApprovalProposal(
                connection_id=conn.id,
                user_id=self.coordinator.user_id,
                run_id=getattr(self.coordinator, 'active_run_id', None),
                tool=tool_name,
                action_digest=policy_result.grant.action_digest,
                policy_version=self.policy_engine.policy_version,
                candidate_set_hash=candidate_set_hash,
                label_delta_add=add_labels,
                label_delta_remove=remove_labels,
                estimated_count=len(message_ids),
                estimated_bytes=total_bytes,
                sample_refs=sample_refs,
                expires_at=policy_result.expires_at,
                status=ProposalStatus.PENDING,
            )
            db.add(proposal)
            await db.flush()

            # Add proposal items
            for item in proposal_items:
                proposal_item = ProposalItem(
                    proposal_id=proposal.id,
                    message_id=item['message_id'],
                    thread_id=item['thread_id'],
                    prior_label_ids=item['prior_label_ids'],
                    size_estimate=item['size_estimate'],
                )
                db.add(proposal_item)

            await db.commit()
            await db.refresh(proposal)

            # Update grant with proposal_id
            policy_result.grant.proposal_id = proposal.id

            return ToolResult(
                success=True,
                data={
                    'proposal_id': str(proposal.id),
                    'tool': tool_name,
                    'message_ids': [item['message_id'] for item in proposal_items],
                    'action_digest': proposal.action_digest,
                    'estimated_count': proposal.estimated_count,
                    'estimated_bytes': proposal.estimated_bytes,
                    'sample_refs': sample_refs,
                    'expires_at': proposal.expires_at.isoformat(),
                    'label_delta_add': add_labels,
                    'label_delta_remove': remove_labels,
                    'requires_approval': True,
                    'message': f'Proposal created for {tool_name}. Requires human approval.',
                },
                metadata={'interrupt': True, 'proposal_id': str(proposal.id)},
            )
