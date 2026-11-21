"""Structural invariants from the architecture: the model can only propose,
never execute. Provider mutations must be unreachable without a PolicyGrant."""

import hashlib
import inspect
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import app.auth.session as session_module
import pytest
from app.auth.session import SessionManager
from app.db.models import OIDCLoginAttempt
from app.providers.base import PolicyGrant
from app.providers.gmail import GmailProvider
from app.tools.registry import TOOL_REGISTRY


def test_tool_registry_has_no_execute_tools():
    execute_tools = [name for name, spec in TOOL_REGISTRY.items() if spec['type'] == 'execute']
    assert (
        execute_tools == []
    ), f'model-visible registry must not expose execute tools: {execute_tools}'


def test_registry_exposes_only_read_derived_propose():
    allowed = {'read', 'derived', 'propose'}
    for name, spec in TOOL_REGISTRY.items():
        assert spec['type'] in allowed, f"{name} has disallowed type {spec['type']}"


def test_provider_mutation_requires_grant():
    sig = inspect.signature(GmailProvider.batch_modify_labels)
    params = list(sig.parameters)
    assert params[1] == 'grant', 'first parameter after self must be the PolicyGrant'
    assert sig.parameters['grant'].annotation is PolicyGrant or 'PolicyGrant' in str(
        sig.parameters['grant'].annotation
    )


def test_grant_is_a_constrained_capability():
    from dataclasses import fields

    names = {f.name for f in fields(PolicyGrant)}
    assert names == {
        'proposal_id',
        'action_digest',
        'connection_id',
        'scopes_checked',
        'expires_at',
    }


def test_proposal_tool_names_match_policy_write_tools():
    propose_tools = {n for n, s in TOOL_REGISTRY.items() if s['type'] == 'propose'}
    assert propose_tools == {'propose_labels', 'propose_archive', 'propose_trash'}


@pytest.mark.asyncio
async def test_oidc_state_is_hashed_and_consumed_once(monkeypatch):
    state = 'high-entropy-state'
    verifier = 'pkce-verifier'
    browser_nonce = 'browser-bound-nonce'

    class FakeDB:
        def __init__(self):
            self.added = []
            self.results = [verifier, None]

        def add(self, value):
            self.added.append(value)

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: self.results.pop(0))

        async def commit(self):
            pass

    fake_db = FakeDB()

    @asynccontextmanager
    async def fake_get_session():
        yield fake_db

    monkeypatch.setattr(session_module, 'get_session', fake_get_session)
    manager = SessionManager()

    await manager.create_oidc_login_attempt(state, verifier, browser_nonce)
    attempt = fake_db.added[0]
    assert isinstance(attempt, OIDCLoginAttempt)
    assert attempt.state_hash == hashlib.sha256(state.encode()).hexdigest()
    assert attempt.state_hash != state
    assert attempt.browser_nonce_hash == hashlib.sha256(browser_nonce.encode()).hexdigest()

    assert await manager.consume_oidc_login_attempt(state, browser_nonce) == verifier
    assert await manager.consume_oidc_login_attempt(state, browser_nonce) is None


@pytest.mark.asyncio
async def test_gmail_mutation_rejects_unscoped_grant_before_provider_access():
    provider = GmailProvider.__new__(GmailProvider)
    provider.settings = SimpleNamespace(gmail_max_messages_per_run=250)
    provider.connection_manager = SimpleNamespace()
    grant = PolicyGrant(
        proposal_id=uuid4(),
        action_digest='digest',
        connection_id=uuid4(),
        scopes_checked=frozenset(),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    result = await provider.batch_modify_labels_with_user(
        grant=grant,
        message_ids=['message-1'],
        add=['TRASH'],
        remove=[],
        user_id=uuid4(),
    )

    assert not result.success
    assert result.error == 'Invalid or expired PolicyGrant'
