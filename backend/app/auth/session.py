import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update

from app.auth.encryption import decrypt_token, encrypt_token
from app.auth.oidc import KeycloakClient, TokenClaims
from app.config import get_settings
from app.db.models import (
    ConnectionAttempt,
    ConnectionStatus,
    EmailConnection,
    OIDCLoginAttempt,
    Session,
    SessionStatus,
    User,
)
from app.db.session import get_session


@dataclass
class SessionData:
    session_id: str
    user_id: UUID
    keycloak_subject: str
    email: str
    roles: list[str]
    csrf_token: str
    expires_at: datetime
    absolute_expires_at: datetime


class SessionManager:
    def __init__(self):
        self.settings = get_settings()
        self.oidc = KeycloakClient()

    def _hash_session_id(self, session_id: str) -> str:
        return hashlib.sha256(session_id.encode()).hexdigest()

    def _generate_csrf_secret(self) -> str:
        return secrets.token_urlsafe(32)

    def _derive_csrf_token(self, csrf_secret: str, session_id: str) -> str:
        return hmac.new(csrf_secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()

    async def create_session(self, claims: TokenClaims) -> SessionData:
        async with get_session() as db:
            # Get or create user
            stmt = select(User).where(User.keycloak_subject == claims.sub)
            result = await db.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                user = User(
                    keycloak_subject=claims.sub,
                    email=claims.email,
                    status='active',
                )
                db.add(user)
                await db.flush()

            # Create session
            session_id = secrets.token_urlsafe(32)
            session_id_hash = self._hash_session_id(session_id)
            csrf_secret = self._generate_csrf_secret()
            csrf_token = self._derive_csrf_token(csrf_secret, session_id)

            now = datetime.now(UTC)
            expires_at = now + timedelta(minutes=self.settings.session_idle_timeout_minutes)
            absolute_expires_at = now + timedelta(
                minutes=self.settings.session_absolute_timeout_minutes
            )

            session = Session(
                id_hash=session_id_hash,
                user_id=user.id,
                csrf_secret=csrf_secret,
                roles=claims.roles,
                auth_time=datetime.fromtimestamp(claims.auth_time, tz=UTC),
                created_at=now,
                expires_at=expires_at,
                absolute_expires_at=absolute_expires_at,
                status=SessionStatus.ACTIVE,
            )
            db.add(session)
            await db.commit()

            return SessionData(
                session_id=session_id,
                user_id=user.id,
                keycloak_subject=claims.sub,
                email=claims.email,
                roles=claims.roles,
                csrf_token=csrf_token,
                expires_at=expires_at,
                absolute_expires_at=absolute_expires_at,
            )

    async def validate_session(self, session_id: str) -> SessionData | None:
        session_id_hash = self._hash_session_id(session_id)

        async with get_session() as db:
            stmt = (
                select(Session, User)
                .join(User)
                .where(
                    Session.id_hash == session_id_hash,
                    Session.status == SessionStatus.ACTIVE,
                )
            )
            result = await db.execute(stmt)
            row = result.first()

            if not row:
                return None

            session, user = row

            now = datetime.now(UTC)
            if session.revoked_at or now > session.expires_at or now > session.absolute_expires_at:
                # Mark as expired
                session.status = SessionStatus.EXPIRED
                await db.commit()
                return None

            # Update last activity (extend idle timeout)
            session.expires_at = now + timedelta(minutes=self.settings.session_idle_timeout_minutes)
            await db.commit()

            csrf_token = self._derive_csrf_token(session.csrf_secret, session_id)

            return SessionData(
                session_id=session_id,
                user_id=user.id,
                keycloak_subject=user.keycloak_subject,
                email=user.email,
                roles=session.roles,
                csrf_token=csrf_token,
                expires_at=session.expires_at,
                absolute_expires_at=session.absolute_expires_at,
            )

    async def revoke_session(self, session_id: str) -> bool:
        session_id_hash = self._hash_session_id(session_id)

        async with get_session() as db:
            stmt = select(Session).where(Session.id_hash == session_id_hash)
            result = await db.execute(stmt)
            session = result.scalar_one_or_none()

            if not session:
                return False

            session.status = SessionStatus.REVOKED
            session.revoked_at = datetime.now(UTC)
            await db.commit()
            return True

    async def revoke_all_user_sessions(self, user_id: UUID) -> int:
        async with get_session() as db:
            stmt = select(Session).where(
                Session.user_id == user_id, Session.status == SessionStatus.ACTIVE
            )
            result = await db.execute(stmt)
            sessions = result.scalars().all()

            count = 0
            for session in sessions:
                session.status = SessionStatus.REVOKED
                session.revoked_at = datetime.now(UTC)
                count += 1

            await db.commit()
            return count

    def verify_csrf(self, session_id: str, csrf_token: str, csrf_secret: str) -> bool:
        expected = self._derive_csrf_token(csrf_secret, session_id)
        return hmac.compare_digest(expected, csrf_token)

    async def get_csrf_secret(self, session_id: str) -> str | None:
        session_id_hash = self._hash_session_id(session_id)

        async with get_session() as db:
            stmt = select(Session.csrf_secret).where(Session.id_hash == session_id_hash)
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def create_oidc_login_attempt(
        self, state: str, code_verifier: str, browser_nonce: str
    ) -> None:
        now = datetime.now(UTC)
        attempt = OIDCLoginAttempt(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            browser_nonce_hash=hashlib.sha256(browser_nonce.encode()).hexdigest(),
            code_verifier=code_verifier,
            expires_at=now + timedelta(minutes=10),
        )
        async with get_session() as db:
            await db.execute(
                delete(OIDCLoginAttempt).where(
                    OIDCLoginAttempt.expires_at < now - timedelta(days=1)
                )
            )
            db.add(attempt)
            await db.commit()

    async def consume_oidc_login_attempt(self, state: str, browser_nonce: str) -> str | None:
        stmt = (
            update(OIDCLoginAttempt)
            .where(
                OIDCLoginAttempt.state_hash == hashlib.sha256(state.encode()).hexdigest(),
                OIDCLoginAttempt.browser_nonce_hash
                == hashlib.sha256(browser_nonce.encode()).hexdigest(),
                OIDCLoginAttempt.consumed_at.is_(None),
                OIDCLoginAttempt.expires_at > datetime.now(UTC),
            )
            .values(consumed_at=datetime.now(UTC))
            .returning(OIDCLoginAttempt.code_verifier)
        )
        async with get_session() as db:
            result = await db.execute(stmt)
            return result.scalar_one_or_none()


class ConnectionManager:
    def __init__(self):
        self.settings = get_settings()

    async def create_connection_attempt(
        self, user_id: UUID, state: str, code_verifier: str, scopes: list[str]
    ) -> ConnectionAttempt:
        async with get_session() as db:
            attempt = ConnectionAttempt(
                user_id=user_id,
                state=state,
                code_verifier=code_verifier,
                requested_scopes=scopes,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
            db.add(attempt)
            await db.commit()
            await db.refresh(attempt)
            return attempt

    async def consume_connection_attempt(self, state: str) -> ConnectionAttempt | None:
        async with get_session() as db:
            stmt = select(ConnectionAttempt).where(
                ConnectionAttempt.state == state,
                ConnectionAttempt.consumed_at.is_(None),
                ConnectionAttempt.expires_at > datetime.now(UTC),
            )
            result = await db.execute(stmt)
            attempt = result.scalar_one_or_none()

            if not attempt:
                return None

            attempt.consumed_at = datetime.now(UTC)
            await db.commit()
            return attempt

    async def store_connection(
        self,
        user_id: UUID,
        provider_account: str,
        scopes: list[str],
        refresh_token: str,
        token_expiry: datetime | None,
    ) -> EmailConnection:
        ciphertext, key_id = encrypt_token(refresh_token)

        async with get_session() as db:
            # Check if connection already exists
            stmt = select(EmailConnection).where(
                EmailConnection.user_id == user_id,
                EmailConnection.provider_account == provider_account,
            )
            result = await db.execute(stmt)
            conn = result.scalar_one_or_none()

            if conn:
                # Update existing
                conn.scopes = scopes
                conn.token_ciphertext = ciphertext
                conn.token_key_id = key_id
                conn.token_expiry = token_expiry
                conn.status = (
                    ConnectionStatus.WRITE_ENABLED
                    if 'https://www.googleapis.com/auth/gmail.modify' in scopes
                    else ConnectionStatus.READ_ONLY
                )
                conn.last_used_at = datetime.now(UTC)
                await db.commit()
                await db.refresh(conn)
                return conn

            conn = EmailConnection(
                user_id=user_id,
                provider_account=provider_account,
                scopes=scopes,
                status=ConnectionStatus.WRITE_ENABLED
                if 'https://www.googleapis.com/auth/gmail.modify' in scopes
                else ConnectionStatus.READ_ONLY,
                token_ciphertext=ciphertext,
                token_key_id=key_id,
                token_expiry=token_expiry,
            )
            db.add(conn)
            await db.commit()
            await db.refresh(conn)
            return conn

    async def get_connection(self, connection_id: UUID, user_id: UUID) -> EmailConnection | None:
        async with get_session() as db:
            stmt = select(EmailConnection).where(
                EmailConnection.id == connection_id,
                EmailConnection.user_id == user_id,
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def get_user_connections(self, user_id: UUID) -> list[EmailConnection]:
        async with get_session() as db:
            stmt = select(EmailConnection).where(EmailConnection.user_id == user_id)
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def get_active_connection(self, user_id: UUID) -> EmailConnection | None:
        async with get_session() as db:
            stmt = (
                select(EmailConnection)
                .where(
                    EmailConnection.user_id == user_id,
                    EmailConnection.status.in_(
                        [ConnectionStatus.READ_ONLY, ConnectionStatus.WRITE_ENABLED]
                    ),
                )
                .order_by(EmailConnection.last_used_at.desc().nullslast())
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def revoke_connection(self, connection_id: UUID, user_id: UUID) -> bool:
        async with get_session() as db:
            stmt = select(EmailConnection).where(
                EmailConnection.id == connection_id,
                EmailConnection.user_id == user_id,
            )
            result = await db.execute(stmt)
            conn = result.scalar_one_or_none()

            if not conn:
                return False

            conn.status = ConnectionStatus.REVOKED
            await db.commit()
            return True

    async def get_decrypted_refresh_token(self, connection_id: UUID, user_id: UUID) -> str | None:
        async with get_session() as db:
            stmt = select(EmailConnection.token_ciphertext, EmailConnection.token_key_id).where(
                EmailConnection.id == connection_id,
                EmailConnection.user_id == user_id,
            )
            result = await db.execute(stmt)
            row = result.first()

            if not row:
                return None

            ciphertext, key_id = row
            return decrypt_token(ciphertext, key_id)

    async def update_connection_token(
        self, connection_id: UUID, user_id: UUID, refresh_token: str, token_expiry: datetime | None
    ) -> bool:
        ciphertext, key_id = encrypt_token(refresh_token)

        async with get_session() as db:
            stmt = select(EmailConnection).where(
                EmailConnection.id == connection_id,
                EmailConnection.user_id == user_id,
            )
            result = await db.execute(stmt)
            conn = result.scalar_one_or_none()

            if not conn:
                return False

            conn.token_ciphertext = ciphertext
            conn.token_key_id = key_id
            conn.token_expiry = token_expiry
            await db.commit()
            return True

    async def mark_connection_used(self, connection_id: UUID) -> None:
        async with get_session() as db:
            stmt = select(EmailConnection).where(EmailConnection.id == connection_id)
            result = await db.execute(stmt)
            conn = result.scalar_one_or_none()

            if conn:
                conn.last_used_at = datetime.now(UTC)
                await db.commit()
