from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.config import get_settings
from app.db.models import ConnectionStatus, EmailConnection
from app.providers.base import PolicyGrant

if TYPE_CHECKING:
    from app.db.models import ApprovalProposal as ApprovalProposalLike


class PolicyDecision(StrEnum):
    ALLOW = 'allow'
    DENY = 'deny'
    REQUIRE_APPROVAL = 'require_approval'


class RiskClass(StrEnum):
    READ = 'read'
    WRITE_LOW = 'write_low'
    WRITE_HIGH = 'write_high'


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reason: str
    risk_class: RiskClass
    requires_approval: bool
    grant: PolicyGrant | None = None
    expires_at: datetime | None = None
    limits: dict[str, Any] | None = None


class PolicyEngine:
    def __init__(self):
        self.settings = get_settings()
        self.policy_version = '2026-11-01'

    def _compute_action_digest(
        self,
        connection_id: UUID,
        tool: str,
        add_labels: list[str],
        remove_labels: list[str],
        message_ids: list[str],
        expires_at: datetime,
    ) -> str:
        import hashlib
        import json

        # Canonical JSON (sorted keys, no whitespace) per RFC 8785
        payload = {
            'v': 1,
            'policy_version': self.policy_version,
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

    def authorize_read(
        self,
        connection: EmailConnection,
        tool: str,
        args: dict[str, Any],
    ) -> PolicyResult:
        """Read operations: check scope coverage and connection status."""
        if connection.status not in (ConnectionStatus.READ_ONLY, ConnectionStatus.WRITE_ENABLED):
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f'Connection status is {connection.status.value}, not connected',
                risk_class=RiskClass.READ,
                requires_approval=False,
            )

        # Check readonly scope
        required_scope = 'https://www.googleapis.com/auth/gmail.readonly'
        if required_scope not in connection.scopes:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f'Missing required scope: {required_scope}',
                risk_class=RiskClass.READ,
                requires_approval=False,
            )

        # Check tool-specific bounds
        max_results = args.get('max_results', 100)
        if max_results > 500:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason='max_results exceeds limit of 500',
                risk_class=RiskClass.READ,
                requires_approval=False,
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason='Read operation authorized',
            risk_class=RiskClass.READ,
            requires_approval=False,
        )

    def authorize_write(
        self,
        connection: EmailConnection,
        tool: str,
        args: dict[str, Any],
    ) -> PolicyResult:
        """Write operations: require gmail.modify scope, check limits, require approval."""
        if connection.status != ConnectionStatus.WRITE_ENABLED:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f'Connection not write-enabled (status: {connection.status.value})',
                risk_class=RiskClass.WRITE_HIGH,
                requires_approval=False,
            )

        # Check modify scope
        required_scope = 'https://www.googleapis.com/auth/gmail.modify'
        if required_scope not in connection.scopes:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f'Missing required scope: {required_scope}',
                risk_class=RiskClass.WRITE_HIGH,
                requires_approval=False,
            )

        # Residual risk: gmail.modify permits send/compose
        # This is documented in architecture and consent screen

        # Determine risk class and limits based on tool
        if tool == 'propose_trash':
            risk_class = RiskClass.WRITE_HIGH
            max_messages = self.settings.gmail_max_messages_per_run
        elif tool in ('propose_archive', 'propose_labels'):
            risk_class = RiskClass.WRITE_LOW
            max_messages = self.settings.gmail_max_messages_per_run
        else:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f'Unknown write tool: {tool}',
                risk_class=RiskClass.WRITE_HIGH,
                requires_approval=False,
            )

        # Extract message IDs from args
        message_ids = args.get('message_ids', [])
        if not message_ids:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason='No message IDs provided',
                risk_class=risk_class,
                requires_approval=False,
            )

        if len(message_ids) > max_messages:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f'Too many messages: {len(message_ids)} > {max_messages}',
                risk_class=risk_class,
                requires_approval=False,
            )

        # Check labels are valid (for propose_labels)
        if tool == 'propose_labels':
            add_labels = args.get('add_labels', [])
            remove_labels = args.get('remove_labels', [])
            if not add_labels and not remove_labels:
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason='No label changes specified',
                    risk_class=risk_class,
                    requires_approval=False,
                )
            # In v1, only existing label IDs allowed - validation happens at execution

        # Normalize the effective label delta before minting the digest.  The
        # executor independently derives these values, so both sides must use
        # the same canonical representation.
        if tool == 'propose_trash':
            add_labels, remove_labels = ['TRASH'], []
        elif tool == 'propose_archive':
            add_labels, remove_labels = [], ['INBOX']
        else:
            add_labels = list(args.get('add_labels', []))
            remove_labels = list(args.get('remove_labels', []))

        # Compute digest for the proposal
        expires_at = datetime.now(UTC) + timedelta(hours=24)  # 24-hour proposal expiry
        action_digest = self._compute_action_digest(
            connection_id=connection.id,
            tool=tool,
            add_labels=add_labels,
            remove_labels=remove_labels,
            message_ids=sorted(message_ids),
            expires_at=expires_at,
        )

        grant = PolicyGrant(
            proposal_id=UUID(int=0),  # Will be set when proposal is created
            action_digest=action_digest,
            connection_id=connection.id,
            scopes_checked=frozenset(connection.scopes),
            expires_at=expires_at,
        )

        return PolicyResult(
            decision=PolicyDecision.REQUIRE_APPROVAL,
            reason=f'Write operation requires approval: {tool}',
            risk_class=risk_class,
            requires_approval=True,
            grant=grant,
            expires_at=expires_at,
            limits={'max_messages': max_messages},
        )

    def authorize_resume(
        self,
        connection: EmailConnection,
        proposal: 'ApprovalProposalLike',
        message_ids: list[str],
    ) -> PolicyResult:
        """Re-authorize an approved proposal against *live* connection state.

        The resume path must never trust the stored proposal alone: the
        connection may have been downgraded or its scopes narrowed since the
        user approved. We re-run the normal authorization, then bind the minted
        grant to the exact approved proposal (digest, expiry, connection) so the
        executor can independently re-derive and compare it.
        """
        if proposal.connection_id != connection.id:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason='Proposal connection does not match the live connection',
                risk_class=RiskClass.WRITE_HIGH,
                requires_approval=False,
            )
        if proposal.policy_version != self.policy_version:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason='Proposal policy version is no longer current',
                risk_class=RiskClass.WRITE_HIGH,
                requires_approval=False,
            )
        if proposal.expires_at <= datetime.now(UTC):
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason='Proposal expired',
                risk_class=RiskClass.WRITE_HIGH,
                requires_approval=False,
            )

        args: dict[str, Any] = {
            'message_ids': message_ids,
            'add_labels': list(proposal.label_delta_add or []),
            'remove_labels': list(proposal.label_delta_remove or []),
        }
        result = self.authorize(connection, proposal.tool, args)

        if result.decision is PolicyDecision.DENY or result.grant is None:
            return result

        grant = replace(
            result.grant,
            proposal_id=proposal.id,
            action_digest=proposal.action_digest,
            connection_id=proposal.connection_id,
            expires_at=proposal.expires_at,
        )
        return replace(result, grant=grant, expires_at=proposal.expires_at)

    def authorize(
        self, connection: EmailConnection, tool: str, args: dict[str, Any]
    ) -> PolicyResult:
        """Main authorization entry point."""
        if tool in (
            'search_messages',
            'get_message',
            'get_thread',
            'list_labels',
            'summarize_selection',
            'group_by_sender',
        ):
            return self.authorize_read(connection, tool, args)
        if tool in ('propose_labels', 'propose_archive', 'propose_trash'):
            return self.authorize_write(connection, tool, args)
        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason=f'Unknown tool: {tool}',
            risk_class=RiskClass.READ,
            requires_approval=False,
        )
