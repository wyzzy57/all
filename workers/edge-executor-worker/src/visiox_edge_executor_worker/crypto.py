from dataclasses import dataclass
import os
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from visiox_common.settings import Settings


_AAD = b"visiox-edge-ssh"
_MASTER_KEY_BYTES = 32
_NONCE_BYTES = 12


@dataclass(frozen=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class CredentialCipher:
    def __init__(self, master_key: bytes, *, key_version: int) -> None:
        if len(master_key) != _MASTER_KEY_BYTES:
            raise ValueError("credential master key must contain exactly 32 raw bytes")
        self._aes = AESGCM(master_key)
        self.key_version = key_version

    @classmethod
    def from_settings(cls, settings: "Settings", *, key_version: int) -> "CredentialCipher":
        return cls(settings.read_edge_credential_master_key(), key_version=key_version)

    def encrypt(self, plaintext: bytes) -> EncryptedSecret:
        nonce = os.urandom(_NONCE_BYTES)
        return EncryptedSecret(
            ciphertext=self._aes.encrypt(nonce, plaintext, _AAD),
            nonce=nonce,
            key_version=self.key_version,
        )

    def decrypt(self, secret: EncryptedSecret) -> bytes:
        if len(secret.nonce) != _NONCE_BYTES:
            raise ValueError("encrypted credential nonce must be 12 bytes")
        return self._aes.decrypt(secret.nonce, secret.ciphertext, _AAD)
