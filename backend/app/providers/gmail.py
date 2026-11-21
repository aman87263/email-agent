from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.auth.session import ConnectionManager
from app.config import get_settings
from app.db.models import ConnectionStatus
from app.providers.base import (
    ChangeResult,
    Label,
    MessageDetail,
    MessageFormat,
    MessageQuery,
    PolicyGrant,
    SearchResult,
    ThreadDetail,
)


class GmailProvider:
    def __init__(self, connection_manager: ConnectionManager):
        self.settings = get_settings()
        self.connection_manager = connection_manager
        self._service_cache: dict[UUID, Any] = {}

    async def _get_service(self, connection_id: UUID, user_id: UUID):
        """Get or build Gmail API service with valid credentials."""
        cache_key = connection_id
        if cache_key in self._service_cache:
            creds = self._service_cache[cache_key]
            if not creds.expired:
                return build('gmail', 'v1', credentials=creds, cache_discovery=False)
            # Try refresh
            try:
                creds.refresh(Request())
                return build('gmail', 'v1', credentials=creds, cache_discovery=False)
            except Exception:
                # Token refresh failed, will re-auth below
                pass

        # Get fresh credentials
        refresh_token = await self.connection_manager.get_decrypted_refresh_token(
            connection_id, user_id
        )
        if not refresh_token:
            raise ValueError('No refresh token available')

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
        )

        try:
            creds.refresh(Request())
        except Exception as e:
            # Mark connection as revoked
            await self.connection_manager.revoke_connection(connection_id, user_id)
            raise ValueError(f'Token refresh failed: {e}') from e

        # Update stored token if it changed
        if creds.refresh_token and creds.refresh_token != refresh_token:
            expiry = None
            if creds.expiry:
                expiry = creds.expiry.replace(tzinfo=UTC)
            await self.connection_manager.update_connection_token(
                connection_id, user_id, creds.refresh_token, expiry
            )

        self._service_cache[cache_key] = creds
        return build('gmail', 'v1', credentials=creds, cache_discovery=False)

    def _parse_message(self, msg: dict, fmt: str) -> MessageDetail:
        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}

        # Extract body
        body_text = None
        body_html = None
        if fmt in (MessageFormat.FULL, MessageFormat.RAW):
            payload = msg.get('payload', {})
            if 'parts' in payload:
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                        import base64

                        body_text = base64.urlsafe_b64decode(part['body']['data']).decode(
                            'utf-8', errors='replace'
                        )
                    elif part.get('mimeType') == 'text/html' and 'data' in part.get('body', {}):
                        import base64

                        body_html = base64.urlsafe_b64decode(part['body']['data']).decode(
                            'utf-8', errors='replace'
                        )
            elif 'body' in payload and 'data' in payload['body']:
                import base64

                data = base64.urlsafe_b64decode(payload['body']['data']).decode(
                    'utf-8', errors='replace'
                )
                if payload.get('mimeType') == 'text/html':
                    body_html = data
                else:
                    body_text = data

        # Parse internal date
        internal_date = None
        if 'internalDate' in msg:
            internal_date = datetime.fromtimestamp(int(msg['internalDate']) / 1000, tz=UTC)

        return MessageDetail(
            id=msg['id'],
            thread_id=msg['threadId'],
            label_ids=msg.get('labelIds', []),
            size_estimate=msg.get('sizeEstimate', 0),
            headers=headers,
            snippet=msg.get('snippet', ''),
            internal_date=internal_date,
            body_text=body_text,
            body_html=body_html,
        )

    def _parse_label(self, label: dict) -> Label:
        return Label(
            id=label['id'],
            name=label['name'],
            type=label.get('type', 'user'),
            messages_total=label.get('messagesTotal'),
            messages_unread=label.get('messagesUnread'),
            threads_total=label.get('threadsTotal'),
            threads_unread=label.get('threadsUnread'),
        )

    async def search_messages(
        self, query: MessageQuery, connection_id: UUID, user_id: UUID
    ) -> SearchResult:
        service = await self._get_service(connection_id, user_id)

        try:
            request = (
                service.users()
                .messages()
                .list(
                    userId='me',
                    q=query.query,
                    maxResults=min(query.max_results, 500),
                    pageToken=query.page_token,
                )
            )
            response = request.execute()
        except HttpError as e:
            if e.resp.status == 401:
                await self.connection_manager.revoke_connection(connection_id, user_id)
            raise

        messages = response.get('messages', [])
        return SearchResult(
            message_ids=[m['id'] for m in messages],
            next_page_token=response.get('nextPageToken'),
            result_size_estimate=response.get('resultSizeEstimate', 0),
        )

    async def get_message(
        self, message_id: str, fmt: str, connection_id: UUID, user_id: UUID
    ) -> MessageDetail:
        service = await self._get_service(connection_id, user_id)

        gmail_fmt = fmt
        if fmt == MessageFormat.METADATA:
            gmail_fmt = 'metadata'
        elif fmt == MessageFormat.FULL:
            gmail_fmt = 'full'
        elif fmt == MessageFormat.MINIMAL:
            gmail_fmt = 'minimal'
        elif fmt == MessageFormat.RAW:
            gmail_fmt = 'raw'

        try:
            request = service.users().messages().get(userId='me', id=message_id, format=gmail_fmt)
            response = request.execute()
        except HttpError as e:
            if e.resp.status == 401:
                await self.connection_manager.revoke_connection(connection_id, user_id)
            raise

        return self._parse_message(response, fmt)

    async def get_thread(
        self, thread_id: str, fmt: str, connection_id: UUID, user_id: UUID
    ) -> ThreadDetail:
        service = await self._get_service(connection_id, user_id)

        gmail_fmt = fmt
        if fmt == MessageFormat.METADATA:
            gmail_fmt = 'metadata'
        elif fmt == MessageFormat.FULL:
            gmail_fmt = 'full'

        try:
            request = service.users().threads().get(userId='me', id=thread_id, format=gmail_fmt)
            response = request.execute()
        except HttpError as e:
            if e.resp.status == 401:
                await self.connection_manager.revoke_connection(connection_id, user_id)
            raise

        messages = [self._parse_message(m, fmt) for m in response.get('messages', [])]
        return ThreadDetail(id=thread_id, messages=messages)

    async def list_labels(self, connection_id: UUID, user_id: UUID) -> list[Label]:
        service = await self._get_service(connection_id, user_id)

        try:
            request = service.users().labels().list(userId='me')
            response = request.execute()
        except HttpError as e:
            if e.resp.status == 401:
                await self.connection_manager.revoke_connection(connection_id, user_id)
            raise

        return [self._parse_label(label) for label in response.get('labels', [])]

    async def batch_modify_labels(
        self,
        grant: PolicyGrant,
        message_ids: Sequence[str],
        add: Sequence[str],
        remove: Sequence[str],
    ) -> ChangeResult:
        # Verify grant matches connection
        if grant.connection_id is None:
            return ChangeResult(
                success=False,
                modified_count=0,
                failed_ids=list(message_ids),
                error='Grant connection mismatch',
            )

        # We need user_id to call the internal method - this will be called from execution layer
        # which has access to user_id
        raise NotImplementedError('Use batch_modify_labels_with_user from execution layer')

    async def batch_modify_labels_with_user(
        self,
        grant: PolicyGrant,
        message_ids: Sequence[str],
        add: Sequence[str],
        remove: Sequence[str],
        user_id: UUID,
    ) -> ChangeResult:
        required_scope = 'https://www.googleapis.com/auth/gmail.modify'
        if (
            grant.proposal_id.int == 0
            or grant.expires_at <= datetime.now(UTC)
            or required_scope not in grant.scopes_checked
        ):
            return ChangeResult(
                success=False,
                modified_count=0,
                failed_ids=list(message_ids),
                error='Invalid or expired PolicyGrant',
            )

        connection = await self.connection_manager.get_connection(grant.connection_id, user_id)
        if (
            connection is None
            or connection.status != ConnectionStatus.WRITE_ENABLED
            or required_scope not in connection.scopes
        ):
            return ChangeResult(
                success=False,
                modified_count=0,
                failed_ids=list(message_ids),
                error='Mailbox connection is not currently write-enabled',
            )

        if not message_ids or len(message_ids) > self.settings.gmail_max_messages_per_run:
            return ChangeResult(
                success=False,
                modified_count=0,
                failed_ids=list(message_ids),
                error='Message count is outside the authorized limit',
            )

        service = await self._get_service(grant.connection_id, user_id)

        body = {
            'ids': list(message_ids),
            'addLabelIds': list(add),
            'removeLabelIds': list(remove),
        }

        try:
            request = service.users().messages().batchModify(userId='me', body=body)
            request.execute()
        except HttpError as e:
            if e.resp.status == 401:
                await self.connection_manager.revoke_connection(grant.connection_id, user_id)
            return ChangeResult(
                success=False,
                modified_count=0,
                failed_ids=list(message_ids),
                error=str(e),
            )

        return ChangeResult(
            success=True,
            modified_count=len(message_ids),
            failed_ids=[],
            error=None,
        )
