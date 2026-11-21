from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass
class PolicyGrant:
    proposal_id: UUID
    action_digest: str
    connection_id: UUID
    scopes_checked: frozenset[str]
    expires_at: datetime


@dataclass
class MessageQuery:
    query: str
    max_results: int = 100
    page_token: str | None = None


@dataclass
class SearchResult:
    message_ids: list[str]
    next_page_token: str | None
    result_size_estimate: int


@dataclass
class MessageDetail:
    id: str
    thread_id: str
    label_ids: list[str]
    size_estimate: int
    headers: dict[str, str]
    snippet: str
    internal_date: datetime | None = None
    body_text: str | None = None
    body_html: str | None = None


@dataclass
class ThreadDetail:
    id: str
    messages: list[MessageDetail]


@dataclass
class Label:
    id: str
    name: str
    type: str  # "system" | "user"
    messages_total: int | None = None
    messages_unread: int | None = None
    threads_total: int | None = None
    threads_unread: int | None = None


@dataclass
class ChangeResult:
    success: bool
    modified_count: int
    failed_ids: list[str]
    error: str | None = None


class MessageFormat:
    METADATA = 'metadata'
    FULL = 'full'
    MINIMAL = 'minimal'
    RAW = 'raw'


class EmailProvider(Protocol):
    async def search_messages(self, query: MessageQuery) -> SearchResult: ...

    async def get_message(self, message_id: str, fmt: str) -> MessageDetail: ...

    async def get_thread(self, thread_id: str, fmt: str) -> ThreadDetail: ...

    async def list_labels(self) -> list[Label]: ...

    async def batch_modify_labels(
        self,
        grant: 'PolicyGrant',
        message_ids: Sequence[str],
        add: Sequence[str],
        remove: Sequence[str],
    ) -> ChangeResult: ...
