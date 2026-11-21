from datetime import datetime
from typing import Any
from uuid import UUID

from app.config import get_settings


class AGUIEventMapper:
    """Maps internal events to AG-UI protocol events."""

    def __init__(self):
        self.settings = get_settings()

    def run_started(
        self,
        thread_id: UUID,
        run_id: UUID,
        messages: list[dict],
        state: dict,
        tools: list[dict],
        context: dict,
    ) -> dict:
        return {
            'type': 'RUN_STARTED',
            'threadId': str(thread_id),
            'runId': str(run_id),
            'messages': messages,
            'state': state,
            'tools': tools,
            'context': context,
        }

    def tool_call_start(
        self, tool_call_id: str, tool_name: str, args: dict, parent_id: str | None = None
    ) -> dict:
        return {
            'type': 'TOOL_CALL_START',
            'toolCallId': tool_call_id,
            'toolName': tool_name,
            'args': args,
            'parentToolCallId': parent_id,
        }

    def tool_call_result(self, tool_call_id: str, result: Any, error: str | None = None) -> dict:
        return {
            'type': 'TOOL_CALL_RESULT',
            'toolCallId': tool_call_id,
            'result': result,
            'error': error,
        }

    def text_message_content(self, message_id: str, delta: str, role: str = 'assistant') -> dict:
        return {
            'type': 'TEXT_MESSAGE_CONTENT',
            'messageId': message_id,
            'delta': delta,
            'role': role,
        }

    def state_snapshot(self, state: dict) -> dict:
        return {
            'type': 'STATE_SNAPSHOT',
            'state': state,
        }

    def messages_snapshot(self, messages: list[dict]) -> dict:
        return {
            'type': 'MESSAGES_SNAPSHOT',
            'messages': messages,
        }

    def run_finished(self, outcome: dict, thread_id: UUID, run_id: UUID) -> dict:
        return {
            'type': 'RUN_FINISHED',
            'threadId': str(thread_id),
            'runId': str(run_id),
            'outcome': outcome,
        }

    def interrupt(self, interrupts: list[dict]) -> dict:
        """Create an interrupt outcome for approval."""
        return {
            'type': 'interrupt',
            'interrupts': interrupts,
        }

    def create_approval_interrupt(
        self,
        proposal_id: UUID,
        tool: str,
        message_ids: list[str],
        estimated_count: int,
        estimated_bytes: int,
        sample_refs: list[dict],
        expires_at: datetime,
        action_digest: str,
        label_delta_add: list[str] | None = None,
        label_delta_remove: list[str] | None = None,
    ) -> dict:
        """Create an AG-UI interrupt for a proposal approval."""
        return {
            'id': str(proposal_id),
            'type': 'approval',
            'tool': tool,
            'title': self._get_interrupt_title(tool),
            'description': self._get_interrupt_description(tool, estimated_count),
            'payload': {
                'proposalId': str(proposal_id),
                'tool': tool,
                'messageIds': message_ids,
                'estimatedCount': estimated_count,
                'estimatedBytes': estimated_bytes,
                'sampleRefs': sample_refs,
                'expiresAt': expires_at.isoformat(),
                'actionDigest': action_digest,
                'labelDeltaAdd': label_delta_add or [],
                'labelDeltaRemove': label_delta_remove or [],
            },
            'responseSchema': {
                'type': 'object',
                'properties': {
                    'approved': {'type': 'boolean'},
                },
                'required': ['approved'],
                'additionalProperties': False,
            },
            'expiresAt': expires_at.isoformat(),
            'metadata': {
                'ag-ui:tool-execution': 'server',
                'proposalId': str(proposal_id),
            },
        }

    def _get_interrupt_title(self, tool: str) -> str:
        titles = {
            'propose_trash': 'Move to Trash',
            'propose_archive': 'Archive Messages',
            'propose_labels': 'Change Labels',
        }
        return titles.get(tool, 'Approve Action')

    def _get_interrupt_description(self, tool: str, count: int) -> str:
        descriptions = {
            'propose_trash': (
                f'Move {count} message(s) to Trash. '
                'This does not free storage until Trash is emptied.'
            ),
            'propose_archive': f'Archive {count} message(s) (remove INBOX label).',
            'propose_labels': f'Change labels on {count} message(s).',
        }
        return descriptions.get(tool, f'Approve action on {count} message(s).')
