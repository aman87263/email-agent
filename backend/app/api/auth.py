import base64
import hashlib
import secrets
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.oidc import KeycloakClient
from app.auth.session import ConnectionManager, SessionData, SessionManager
from app.config import get_settings

router = APIRouter(tags=['auth'])

SameSite = Literal['lax', 'strict', 'none']


def _samesite(value: str) -> SameSite:
    """Narrow the settings string to the Literal starlette expects."""
    return cast(SameSite, value)


def get_session_manager() -> SessionManager:
    return SessionManager()


def get_connection_manager() -> ConnectionManager:
    return ConnectionManager()


def get_oidc_client() -> KeycloakClient:
    return KeycloakClient()


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


async def get_csrf_token(
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> str:
    """Verify CSRF token on state-changing requests."""
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


# Schemas
class SessionResponse(BaseModel):
    user: dict
    roles: list[str]
    csrfToken: str  # noqa: N815  (public contract field consumed by the SPA)


def _oidc_state_cookie_name(state: str) -> str:
    state_suffix = hashlib.sha256(state.encode()).hexdigest()[:16]
    return f'__Secure-ea_oidc_{state_suffix}'


@router.get('/auth/login')
async def login(
    oidc: KeycloakClient = Depends(get_oidc_client),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Initiate Keycloak OIDC login flow. Redirects to Keycloak."""
    state = secrets.token_urlsafe(32)
    browser_nonce = secrets.token_urlsafe(32)
    # PKCE
    code_verifier = secrets.token_urlsafe(64)
    challenge_digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_digest).decode().rstrip('=')
    await session_manager.create_oidc_login_attempt(state, code_verifier, browser_nonce)

    redirect_url = await oidc.get_authorization_url(state, code_challenge)
    response = RedirectResponse(url=redirect_url, status_code=302)
    settings = get_settings()
    response.set_cookie(
        key=_oidc_state_cookie_name(state),
        value=browser_nonce,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=_samesite(settings.session_cookie_samesite),
        path=settings.session_cookie_path,
        max_age=600,
    )
    return response


@router.get('/auth/callback')
async def callback(
    request: Request,
    code: str,
    state: str,
    session_manager: SessionManager = Depends(get_session_manager),
    oidc: KeycloakClient = Depends(get_oidc_client),
):
    """Handle Keycloak OIDC callback."""
    cookie_name = _oidc_state_cookie_name(state)
    browser_nonce = request.cookies.get(cookie_name)
    if not browser_nonce:
        raise HTTPException(status_code=400, detail='Invalid or expired state parameter')

    code_verifier = await session_manager.consume_oidc_login_attempt(state, browser_nonce)
    if not code_verifier:
        raise HTTPException(status_code=400, detail='Invalid or expired state parameter')

    # Exchange code for tokens
    claims = await oidc.exchange_code(code, code_verifier, get_settings().keycloak_redirect_uri)

    # Create session
    session_data = await session_manager.create_session(claims)

    # Set session cookie and redirect to SPA
    response = RedirectResponse(url=get_settings().frontend_url, status_code=302)
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_data.session_id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=_samesite(settings.session_cookie_samesite),
        path=settings.session_cookie_path,
        max_age=settings.session_absolute_timeout_minutes * 60,
    )
    response.delete_cookie(
        key=cookie_name,
        path=settings.session_cookie_path,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=_samesite(settings.session_cookie_samesite),
    )
    return response


@router.post('/auth/logout')
async def logout(
    request: Request,
    response: Response,
    session: SessionData = Depends(get_current_session),
    session_manager: SessionManager = Depends(get_session_manager),
    oidc: KeycloakClient = Depends(get_oidc_client),
    _csrf: str = Depends(get_csrf_token),
):
    """Logout - revoke session and redirect to Keycloak end_session_endpoint."""
    await session_manager.revoke_session(session.session_id)

    settings = get_settings()
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=settings.session_cookie_path,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=_samesite(settings.session_cookie_samesite),
    )

    # Redirect to Keycloak logout
    logout_url = oidc.get_logout_url(settings.frontend_url)
    response.headers['Location'] = logout_url
    response.status_code = 302
    return response


@router.get('/v1/auth/session', response_model=SessionResponse)
async def get_session_info(
    session: SessionData = Depends(get_current_session),
):
    """Get current session info for SPA bootstrap."""
    return SessionResponse(
        user={
            'id': str(session.user_id),
            'email': session.email,
            'keycloak_subject': session.keycloak_subject,
        },
        roles=session.roles,
        csrfToken=session.csrf_token,
    )
