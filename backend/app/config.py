from functools import lru_cache
from typing import Annotated

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        case_sensitive=False,
    )

    # Application
    app_name: str = 'Email Agent API'
    environment: str = 'development'
    debug: bool = False
    root_path: str = '/mail/api'

    # Server
    host: str = '0.0.0.0'
    port: int = 8000
    workers: int = 2

    # Database
    database_url: Annotated[PostgresDsn, Field(description='PostgreSQL connection URL')]
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # Keycloak (shared instance)
    keycloak_issuer: str = 'https://capriman27.duckdns.org/auth/realms/email-agent'
    keycloak_jwks_url: str = (
        'https://capriman27.duckdns.org/auth/realms/email-agent/protocol/openid-connect/certs'
    )
    keycloak_client_id: str = 'email-agent-bff'
    keycloak_required_role: str = 'email-agent-user'
    keycloak_client_secret: str
    keycloak_audience: str = 'email-agent-api'
    keycloak_end_session_endpoint: str = (
        'https://capriman27.duckdns.org/auth/realms/email-agent/protocol/openid-connect/logout'
    )
    keycloak_redirect_uri: str = 'https://capriman27.duckdns.org/mail/api/auth/callback'

    # Session
    session_cookie_name: str = '__Secure-ea_session'
    session_cookie_path: str = '/mail'
    session_idle_timeout_minutes: int = 480  # 8 hours
    session_absolute_timeout_minutes: int = 720  # 12 hours
    session_cookie_secure: bool = True
    session_cookie_samesite: str = 'lax'

    # CSRF
    csrf_secret_key: str

    # Google OAuth
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = (
        'https://capriman27.duckdns.org/mail/api/v1/connections/gmail/callback'
    )
    google_scopes_readonly: tuple[str, ...] = ('https://www.googleapis.com/auth/gmail.readonly',)
    google_scopes_modify: tuple[str, ...] = ('https://www.googleapis.com/auth/gmail.modify',)

    # Encryption (KEK for envelope encryption)
    kek_base64: str = Field(description='Base64-encoded 32-byte Key Encryption Key')
    kek_key_id: str = 'kek-v1'

    # Gmail API
    gmail_quota_per_minute: int = 6000
    gmail_max_full_fetch_per_run: int = 25
    gmail_max_messages_per_run: int = 250
    gmail_chunk_size: int = 200

    # Agent
    agent_max_tool_steps: int = 20
    agent_max_wall_clock_seconds: int = 120
    agent_max_tokens_per_step: int = 4000

    # AG-UI
    agui_version: str = '0.1.0'

    # Observability
    log_level: str = 'INFO'
    otlp_endpoint: str | None = None

    # Frontend URL (for CORS)
    frontend_url: str = 'https://capriman27.duckdns.org/mail/'


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # env-driven required fields
