import base64
import os

import pytest

# Test credentials must be set before app.config is imported (lru_cache).
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost:5432/test')
os.environ.setdefault('KEYCLOAK_CLIENT_SECRET', 'test-client-secret')
os.environ.setdefault('CSRF_SECRET_KEY', 'test-csrf-secret-key-32-bytes-minimum-padding')
os.environ.setdefault('GOOGLE_CLIENT_ID', 'test-google-client-id')
os.environ.setdefault('GOOGLE_CLIENT_SECRET', 'test-google-client-secret')
os.environ.setdefault('KEK_BASE64', base64.b64encode(b'k' * 32).decode())
os.environ.setdefault('ENVIRONMENT', 'test')


@pytest.fixture
def kek_base64() -> str:
    return os.environ['KEK_BASE64']
