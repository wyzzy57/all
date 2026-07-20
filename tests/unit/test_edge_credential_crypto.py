from cryptography.exceptions import InvalidTag
import pytest

from visiox_edge_executor_worker.crypto import CredentialCipher


def test_credential_cipher_round_trip_uses_fresh_gcm_nonces() -> None:
    cipher = CredentialCipher(b"a" * 32, key_version=1)

    first = cipher.encrypt(b"private")
    second = cipher.encrypt(b"private")

    assert cipher.decrypt(first) == b"private"
    assert cipher.decrypt(second) == b"private"
    assert first.key_version == 1
    assert len(first.nonce) == 12
    assert first.nonce != second.nonce


def test_credential_cipher_rejects_wrong_key_without_swallowing_invalid_tag() -> None:
    encrypted = CredentialCipher(b"a" * 32, key_version=1).encrypt(b"private")

    with pytest.raises(InvalidTag):
        CredentialCipher(b"b" * 32, key_version=1).decrypt(encrypted)


@pytest.mark.parametrize("master_key", [b"", b"a" * 31, b"a" * 33])
def test_credential_cipher_requires_exactly_32_raw_master_key_bytes(master_key: bytes) -> None:
    with pytest.raises(ValueError, match="32 raw bytes"):
        CredentialCipher(master_key, key_version=1)
