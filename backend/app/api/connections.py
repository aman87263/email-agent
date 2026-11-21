import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel

from app.auth.session import ConnectionManager, SessionData, SessionManager
from app.config import get_settings
from app.db.models import ConnectionStatus

router = APIRouter(prefix='/v1/connections', tags=['connections'])


class ConnectionResponse(BaseModel):
    id: UUID
    provider_account: str
    scopes: list[str]
    status: str
    connected_at: datetime
    last_used_at: datetime | None


class ConnectionStartResponse(BaseModel):
    consent_url: str
    state: str


class GmailCallbackRequest(BaseModel):
    code: str
    state: str


async def get_connection_manager() -> ConnectionManager:
    return ConnectionManager()


async def get_session_manager() -> SessionManager:
    return SessionManager()


async def get_current_session(
    request: Request,
    session_manager: SessionManager = Depends(get_session_manager),
) -> SessionData:
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise HTTPException(status_code=401, detail='Not authenticated')

    session = await session_manager.validate_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail='Session expired or invalid')

    return session


def get_csrf_dependency():
    async def _verify_csrf(request: Request, session: SessionData = Depends(get_current_session)):
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            csrf_token = request.headers.get('X-CSRF-Token')
            if not csrf_token:
                raise HTTPException(status_code=403, detail='CSRF token required')

            session_manager = SessionManager()
            csrf_secret = await session_manager.get_csrf_secret(session.session_id)
            if not csrf_secret or not session_manager.verify_csrf(
                session.session_id, csrf_token, csrf_secret
            ):
                raise HTTPException(status_code=403, detail='Invalid CSRF token')
        return session.csrf_token

    return _verify_csrf


@router.get('', response_model=list[ConnectionResponse])
async def list_connections(
    session: SessionData = Depends(get_current_session),
    conn_manager: ConnectionManager = Depends(get_connection_manager),
):
    """List all Gmail connections for the current user."""
    connections = await conn_manager.get_user_connections(session.user_id)
    return [
        ConnectionResponse(
            id=c.id,
            provider_account=c.provider_account,
            scopes=c.scopes,
            status=c.status.value,
            connected_at=c.connected_at,
            last_used_at=c.last_used_at,
        )
        for c in connections
    ]


@router.post('/gmail/start', response_model=ConnectionStartResponse)
async def start_gmail_connection(
    request: Request,
    session: SessionData = Depends(get_current_session),
    conn_manager: ConnectionManager = Depends(get_connection_manager),
    _csrf: str = Depends(get_csrf_dependency()),
):
    """Start Gmail OAuth flow - returns consent URL."""
    settings = get_settings()

    # Generate state and PKCE verifier
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)

    # Request readonly scope initially
    scopes = list(settings.google_scopes_readonly)

    # Store attempt
    await conn_manager.create_connection_attempt(session.user_id, state, code_verifier, scopes)

    # Build consent URL (Flow derives the S256 challenge from code_verifier)
    flow = Flow.from_client_config(
        {
            'web': {
                'client_id': settings.google_client_id,
                'client_secret': settings.google_client_secret,
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        },
        scopes=scopes,
        redirect_uri=settings.google_redirect_uri,
    )
    flow.code_verifier = code_verifier

    consent_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        state=state,
        prompt='consent',
    )

    return ConnectionStartResponse(consent_url=consent_url, state=state)


@router.get('/gmail/callback')
async def gmail_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    conn_manager: ConnectionManager = Depends(get_connection_manager),
):
    """Handle Gmail OAuth callback - this is a cross-site GET navigation."""
    # Consume the connection attempt
    attempt = await conn_manager.consume_connection_attempt(state)
    if not attempt:
        raise HTTPException(status_code=400, detail='Invalid or expired state')

    settings = get_settings()

    # Exchange code for tokens (restore the PKCE verifier saved at start)
    flow = Flow.from_client_config(
        {
            'web': {
                'client_id': settings.google_client_id,
                'client_secret': settings.google_client_secret,
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        },
        scopes=attempt.requested_scopes,
        redirect_uri=settings.google_redirect_uri,
    )
    flow.code_verifier = attempt.code_verifier
    flow.fetch_token(code=code)

    credentials = flow.credentials

    # Get user's email address from Gmail API
    from googleapiclient.discovery import build

    service = build('gmail', 'v1', credentials=credentials)
    profile = service.users().getProfile(userId='me').execute()
    provider_account = profile.get('emailAddress', 'unknown')

    # Calculate token expiry
    token_expiry = None
    if credentials.expiry:
        token_expiry = credentials.expiry.replace(tzinfo=UTC)

    # Store connection
    await conn_manager.store_connection(
        user_id=attempt.user_id,
        provider_account=provider_account,
        scopes=attempt.requested_scopes,
        refresh_token=credentials.refresh_token,
        token_expiry=token_expiry,
    )

    # Redirect back to SPA
    response = Response(status_code=302)
    response.headers['Location'] = (
        f'{settings.frontend_url}connections?connected={provider_account}'
    )
    return response


@router.post('/gmail/enable-write')
async def enable_write_access(
    request: Request,
    connection_id: UUID,
    session: SessionData = Depends(get_current_session),
    conn_manager: ConnectionManager = Depends(get_connection_manager),
    _csrf: str = Depends(get_csrf_dependency()),
):
    """Upgrade connection to write access (gmail.modify scope)."""
    settings = get_settings()

    # Verify connection exists and belongs to user
    conn = await conn_manager.get_connection(connection_id, session.user_id)
    if not conn:
        raise HTTPException(status_code=404, detail='Connection not found')

    if conn.status != ConnectionStatus.READ_ONLY:
        raise HTTPException(
            status_code=400, detail='Connection already has write access or is revoked'
        )

    # Generate new state and PKCE for incremental auth
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)

    # Request both scopes (incremental auth)
    scopes = list(settings.google_scopes_readonly) + list(settings.google_scopes_modify)

    # Store attempt
    await conn_manager.create_connection_attempt(session.user_id, state, code_verifier, scopes)

    # Build consent URL for incremental authorization
    flow = Flow.from_client_config(
        {
            'web': {
                'client_id': settings.google_client_id,
                'client_secret': settings.google_client_secret,
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        },
        scopes=scopes,
        redirect_uri=settings.google_redirect_uri,
    )
    flow.code_verifier = code_verifier

    consent_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        state=state,
        prompt='consent',
    )

    return {'consent_url': consent_url, 'state': state}


@router.delete('/{connection_id}')
async def delete_connection(
    connection_id: UUID,
    session: SessionData = Depends(get_current_session),
    conn_manager: ConnectionManager = Depends(get_connection_manager),
    _csrf: str = Depends(get_csrf_dependency()),
):
    """Revoke a Gmail connection."""
    success = await conn_manager.revoke_connection(connection_id, session.user_id)
    if not success:
        raise HTTPException(status_code=404, detail='Connection not found')
    return {'status': 'revoked'}
