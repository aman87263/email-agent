import base64

import pytest
from app.auth.encryption import EncryptionError, decrypt_token, encrypt_token, rotate_kek


def test_encrypt_decrypt_roundtrip():
    plaintext = 'ya29.a0AfH6SMBx-refresh-token-value'
    ciphertext, key_id = encrypt_token(plaintext)
    assert ciphertext != plaintext
    assert key_id
    assert decrypt_token(ciphertext, key_id) == plaintext


def test_ciphertext_is_nondeterministic():
    plaintext = 'same-token'
    a, _ = encrypt_token(plaintext)
    b, _ = encrypt_token(plaintext)
    assert a != b, 'random salt/nonce must make ciphertexts differ'


def test_tampered_ciphertext_fails():
    ciphertext, key_id = encrypt_token('secret')
    raw = bytearray(base64.b64decode(ciphertext))
    raw[-1] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(EncryptionError):
        decrypt_token(tampered, key_id)


def test_wrong_key_id_fails():
    ciphertext, _ = encrypt_token('secret')
    with pytest.raises(EncryptionError):
        decrypt_token(ciphertext, 'kek-other')


def test_kek_rotation_preserves_plaintext():
    old_kek = base64.b64encode(b'o' * 32).decode()
    new_kek = base64.b64encode(b'n' * 32).decode()

    # Encrypt under the old KEK by temporarily overriding settings
    from app.config import get_settings

    settings = get_settings()
    original = settings.kek_base64
    try:
        settings.kek_base64 = old_kek
        ciphertext, key_id = encrypt_token('rotate-me', key_id='kek-v1')
    finally:
        settings.kek_base64 = original

    rotated, new_key_id = rotate_kek(old_kek, new_kek, ciphertext, key_id)
    assert new_key_id == key_id

    try:
        settings.kek_base64 = new_kek
        assert decrypt_token(rotated, new_key_id) == 'rotate-me'
    finally:
        settings.kek_base64 = original
