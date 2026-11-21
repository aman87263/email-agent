import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from app.config import get_settings


@dataclass
class TokenClaims:
    sub: str
    email: str
    roles: list[str]
    auth_time: int
    exp: int
    azp: str
    aud: list[str]
    iss: str


class KeycloakClient:
    def __init__(self):
        self.settings = get_settings()
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_fetched_at: float = 0
        self._jwks_ttl = 3600  # 1 hour

    async def get_authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            'client_id': self.settings.keycloak_client_id,
            'response_type': 'code',
            'scope': 'openid email profile',
            'redirect_uri': self.settings.keycloak_redirect_uri,
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }
        return f'{self.settings.keycloak_issuer}/protocol/openid-connect/auth?{urlencode(params)}'

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenClaims:
        async with httpx.AsyncClient(timeout=10.0) as client:
            data = {
                'grant_type': 'authorization_code',
                'client_id': self.settings.keycloak_client_id,
                'client_secret': self.settings.keycloak_client_secret,
                'code': code,
                'code_verifier': code_verifier,
                'redirect_uri': redirect_uri,
            }
            resp = await client.post(
                f'{self.settings.keycloak_issuer}/protocol/openid-connect/token',
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            resp.raise_for_status()
            token_data = resp.json()

        # We only need the ID token for claims; access/refresh tokens are discarded
        id_token = token_data.get('id_token')
        if not id_token:
            raise ValueError('No id_token in Keycloak response')

        return await self.validate_token(id_token)

    async def validate_token(self, token: str) -> TokenClaims:
        # Get JWKS
        jwks = await self._get_jwks()

        # Get kid from header
        header = jwt.get_unverified_header(token)
        kid = header.get('kid')
        if not kid:
            raise JWTError('Token missing kid')

        # Find key
        key = next((k for k in jwks.get('keys', []) if k.get('kid') == kid), None)
        if not key:
            # Refresh JWKS and try once more
            self._jwks_cache = None
            jwks = await self._get_jwks()
            key = next((k for k in jwks.get('keys', []) if k.get('kid') == kid), None)
            if not key:
                raise JWTError(f'Key with kid={kid} not found in JWKS')

        # Decode and validate
        # ID token audience is the client_id; access tokens carry the audience-mapped value.
        claims = jwt.decode(
            token,
            key,
            algorithms=['RS256'],
            audience=self.settings.keycloak_client_id,
            issuer=self.settings.keycloak_issuer,
            options={'verify_aud': True, 'verify_iss': True, 'verify_exp': True},
        )

        # Additional validations
        if claims.get('azp') != self.settings.keycloak_client_id:
            raise JWTError(f"Invalid azp: {claims.get('azp')}")

        if claims.get('typ') == 'Refresh':
            raise JWTError('Refresh token presented as access token')

        return TokenClaims(
            sub=claims['sub'],
            email=claims.get('email', ''),
            roles=claims.get('realm_access', {}).get('roles', []),
            auth_time=claims.get('auth_time', int(time.time())),
            exp=claims['exp'],
            azp=claims['azp'],
            aud=claims.get('aud', []),
            iss=claims['iss'],
        )

    async def _get_jwks(self) -> dict[str, Any]:
        now = time.time()
        if self._jwks_cache and (now - self._jwks_fetched_at) < self._jwks_ttl:
            return self._jwks_cache

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self.settings.keycloak_jwks_url)
            resp.raise_for_status()
            jwks: dict[str, Any] = resp.json()
            self._jwks_cache = jwks
            self._jwks_fetched_at = now
            return jwks

    def get_logout_url(self, post_logout_redirect_uri: str) -> str:
        params = {
            'client_id': self.settings.keycloak_client_id,
            'post_logout_redirect_uri': post_logout_redirect_uri,
        }
        return f'{self.settings.keycloak_end_session_endpoint}?{urlencode(params)}'
