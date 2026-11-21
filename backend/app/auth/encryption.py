import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings


class EncryptionError(Exception):
    pass


def _get_kek() -> bytes:
    settings = get_settings()
    kek = base64.b64decode(settings.kek_base64)
    if len(kek) != 32:
        raise EncryptionError('KEK must be 32 bytes (base64-encoded)')
    return kek


def _derive_dek(kek: bytes, key_id: str, salt: bytes) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=key_id.encode(),
    )
    return hkdf.derive(kek)


def encrypt_token(plaintext: str, key_id: str | None = None) -> tuple[str, str]:
    """Encrypt a token using envelope encryption. Returns (ciphertext_b64, key_id)."""
    settings = get_settings()
    kek = _get_kek()
    key_id = key_id or settings.kek_key_id

    # Generate random salt and DEK
    salt = os.urandom(16)
    dek = _derive_dek(kek, key_id, salt)

    # Encrypt with AES-GCM
    aesgcm = AESGCM(dek)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)

    # Format: salt (16) + nonce (12) + ciphertext
    combined = salt + nonce + ciphertext
    return base64.b64encode(combined).decode(), key_id


def decrypt_token(ciphertext_b64: str, key_id: str) -> str:
    """Decrypt a token using envelope encryption."""
    kek = _get_kek()
    combined = base64.b64decode(ciphertext_b64)

    if len(combined) < 28:  # salt(16) + nonce(12) minimum
        raise EncryptionError('Invalid ciphertext format')

    salt = combined[:16]
    nonce = combined[16:28]
    ciphertext = combined[28:]

    dek = _derive_dek(kek, key_id, salt)
    aesgcm = AESGCM(dek)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise EncryptionError('Ciphertext authentication failed') from exc
    return plaintext.decode()


def rotate_kek(
    old_kek_b64: str, new_kek_b64: str, ciphertext_b64: str, key_id: str
) -> tuple[str, str]:
    """Re-encrypt with a new KEK. Returns (new_ciphertext_b64, new_key_id)."""
    old_kek = base64.b64decode(old_kek_b64)
    new_kek = base64.b64decode(new_kek_b64)

    combined = base64.b64decode(ciphertext_b64)
    salt = combined[:16]
    nonce = combined[16:28]
    ciphertext = combined[28:]

    # Derive old DEK and decrypt
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=key_id.encode())
    old_dek = hkdf.derive(old_kek)
    aesgcm = AESGCM(old_dek)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    # Encrypt with new KEK (new salt, new DEK)
    new_salt = os.urandom(16)
    new_hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=new_salt, info=key_id.encode())
    new_dek = new_hkdf.derive(new_kek)
    new_aesgcm = AESGCM(new_dek)
    new_nonce = os.urandom(12)
    new_ciphertext = new_aesgcm.encrypt(new_nonce, plaintext, None)

    new_combined = new_salt + new_nonce + new_ciphertext
    return base64.b64encode(new_combined).decode(), key_id
