from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.db.models import ConnectionStatus, EmailConnection
from app.policy.engine import PolicyDecision, PolicyEngine, RiskClass


def make_connection(
    status: ConnectionStatus = ConnectionStatus.WRITE_ENABLED,
    scopes: list[str] | None = None,
) -> EmailConnection:
    scopes = (
        scopes
        if scopes is not None
        else [
            'https://www.googleapis.com/auth/gmail.readonly',
            'https://www.googleapis.com/auth/gmail.modify',
        ]
    )
    return EmailConnection(
        id=uuid4(),
        user_id=uuid4(),
        provider_account='user@example.com',
        scopes=scopes,
        status=status,
        token_ciphertext='ciphertext',
        token_key_id='kek-v1',
    )


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine()


def test_read_allowed_with_readonly_scope(engine):
    conn = make_connection(scopes=['https://www.googleapis.com/auth/gmail.readonly'])
    result = engine.authorize(conn, 'search_messages', {'query': 'in:inbox', 'max_results': 50})
    assert result.decision is PolicyDecision.ALLOW
    assert result.risk_class is RiskClass.READ
    assert not result.requires_approval


def test_read_denied_without_readonly_scope(engine):
    conn = make_connection(scopes=[])
    result = engine.authorize(conn, 'search_messages', {'query': 'in:inbox'})
    assert result.decision is PolicyDecision.DENY


def test_read_denied_when_connection_revoked(engine):
    conn = make_connection(status=ConnectionStatus.REVOKED)
    result = engine.authorize(conn, 'search_messages', {'query': 'in:inbox'})
    assert result.decision is PolicyDecision.DENY


def test_read_max_results_cap(engine):
    conn = make_connection()
    result = engine.authorize(conn, 'search_messages', {'query': 'in:inbox', 'max_results': 501})
    assert result.decision is PolicyDecision.DENY


def test_write_requires_modify_scope(engine):
    conn = make_connection(scopes=['https://www.googleapis.com/auth/gmail.readonly'])
    result = engine.authorize(conn, 'propose_archive', {'message_ids': ['m1']})
    assert result.decision is PolicyDecision.DENY


def test_write_requires_write_enabled_status(engine):
    conn = make_connection(status=ConnectionStatus.READ_ONLY)
    result = engine.authorize(conn, 'propose_archive', {'message_ids': ['m1']})
    assert result.decision is PolicyDecision.DENY


def test_write_returns_grant_requiring_approval(engine):
    conn = make_connection()
    result = engine.authorize(conn, 'propose_archive', {'message_ids': ['m1', 'm2']})
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert result.requires_approval
    assert result.grant is not None
    assert result.grant.connection_id == conn.id
    assert result.expires_at > datetime.now(UTC)


def test_write_rejects_empty_message_set(engine):
    conn = make_connection()
    result = engine.authorize(conn, 'propose_trash', {'message_ids': []})
    assert result.decision is PolicyDecision.DENY


def test_write_enforces_message_cap(engine):
    conn = make_connection()
    result = engine.authorize(conn, 'propose_trash', {'message_ids': [str(i) for i in range(251)]})
    assert result.decision is PolicyDecision.DENY


def test_trash_is_high_risk(engine):
    conn = make_connection()
    result = engine.authorize(conn, 'propose_trash', {'message_ids': ['m1']})
    assert result.risk_class is RiskClass.WRITE_HIGH


def test_archive_is_low_risk(engine):
    conn = make_connection()
    result = engine.authorize(conn, 'propose_archive', {'message_ids': ['m1']})
    assert result.risk_class is RiskClass.WRITE_LOW


def test_unknown_tool_denied(engine):
    conn = make_connection()
    result = engine.authorize(conn, 'execute_delete_everything', {'message_ids': ['m1']})
    assert result.decision is PolicyDecision.DENY


def test_digest_is_stable_across_key_order(engine):
    conn = make_connection()
    expires = datetime.now(UTC) + timedelta(hours=1)
    a = engine._compute_action_digest(
        conn.id, 'propose_labels', ['A'], ['B'], ['m2', 'm1'], expires
    )
    b = engine._compute_action_digest(
        conn.id, 'propose_labels', ['A'], ['B'], ['m1', 'm2'], expires
    )
    assert a == b, 'message IDs must be canonicalised before hashing'


def test_digest_changes_with_labels(engine):
    conn = make_connection()
    expires = datetime.now(UTC) + timedelta(hours=1)
    a = engine._compute_action_digest(conn.id, 'propose_labels', ['A'], [], ['m1'], expires)
    b = engine._compute_action_digest(conn.id, 'propose_labels', ['B'], [], ['m1'], expires)
    assert a != b
