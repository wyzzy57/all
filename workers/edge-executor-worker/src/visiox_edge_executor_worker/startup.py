from dataclasses import dataclass

from visiox_common.settings import Settings

from .crypto import CredentialCipher
from .ssh import StrictSshClient


@dataclass(frozen=True)
class EdgeExecutorSecurityContext:
    credential_cipher: CredentialCipher
    ssh_client: StrictSshClient


def initialize_security(settings: Settings) -> EdgeExecutorSecurityContext:
    credential_cipher = CredentialCipher.from_settings(settings, key_version=1)
    ssh_client = StrictSshClient(
        connect_timeout_seconds=settings.edge_ssh_connect_timeout_seconds,
        auth_timeout_seconds=settings.edge_ssh_auth_timeout_seconds,
        banner_timeout_seconds=settings.edge_ssh_banner_timeout_seconds,
        max_output_bytes=settings.edge_ssh_max_output_bytes,
    )
    return EdgeExecutorSecurityContext(
        credential_cipher=credential_cipher,
        ssh_client=ssh_client,
    )
