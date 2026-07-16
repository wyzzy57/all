import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import ValidationError

from visiox_api.services.agent_identity import (
    AgentIdentityError,
    ensure_agent_ca,
    hash_enrollment_token,
    issue_agent_certificate,
    verify_agent_signature,
)
from visiox_common.settings import Settings


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "agent_ca_cert_path": tmp_path / "ca.crt",
        "agent_ca_key_path": tmp_path / "ca.key",
        "agent_auto_generate_ca": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _csr() -> tuple[ed25519.Ed25519PrivateKey, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ignored-edge-name")]))
        .sign(key, algorithm=None)
    )
    return key, csr.public_bytes(serialization.Encoding.PEM).decode()


def _load_ca(settings: Settings) -> tuple[ed25519.Ed25519PrivateKey, x509.Certificate]:
    key = serialization.load_pem_private_key(settings.agent_ca_key_path.read_bytes(), password=None)
    assert isinstance(key, ed25519.Ed25519PrivateKey)
    certificate = x509.load_pem_x509_certificate(settings.agent_ca_cert_path.read_bytes())
    return key, certificate


def _certificate(
    settings: Settings,
    agent_key: ed25519.Ed25519PrivateKey,
    *,
    now: datetime,
    common_name: str | None = "node-123",
    not_before: datetime | None = None,
    not_after: datetime | None = None,
    eku: x509.ObjectIdentifier = ExtendedKeyUsageOID.CLIENT_AUTH,
    signer: ed25519.Ed25519PrivateKey | None = None,
) -> str:
    ca_key, ca_certificate = _load_ca(settings)
    subject_attributes = [] if common_name is None else [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name(subject_attributes))
        .issuer_name(ca_certificate.subject)
        .public_key(agent_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or now - timedelta(minutes=1))
        .not_valid_after(not_after or now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
        .sign(signer or ca_key, algorithm=None)
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def test_ca_issues_node_certificate_and_verifies_nonce(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)
    key, csr_pem = _csr()
    now = datetime.now(UTC)

    issued = issue_agent_certificate(csr_pem, "node-123", settings, now=now)
    nonce = b"challenge"
    verified = verify_agent_signature(
        issued.certificate_pem,
        nonce,
        key.sign(nonce),
        settings,
        now=now,
    )

    certificate = x509.load_pem_x509_certificate(issued.certificate_pem.encode())
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "node-123"
    assert certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value == x509.ExtendedKeyUsage(
        [ExtendedKeyUsageOID.CLIENT_AUTH]
    )
    assert verified.node_id == "node-123"
    assert verified.serial_number == issued.serial_number
    assert verified.fingerprint_sha256 == issued.fingerprint_sha256
    assert issued.ca_certificate_pem == settings.agent_ca_cert_path.read_text()
    assert issued.expires_at == now + timedelta(days=settings.agent_certificate_ttl_days)
    assert hash_enrollment_token("secret") == hashlib.sha256(b"secret").hexdigest()


def test_ensure_agent_ca_rejects_missing_material_when_generation_is_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path, agent_auto_generate_ca=False)

    with pytest.raises(RuntimeError, match="^Agent CA material is required$"):
        ensure_agent_ca(settings)


def test_ensure_agent_ca_fails_closed_outside_local_environment(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        VISIOX_ENV="production",
        agent_public_ws_url="wss://visiox.example/agent/v1/connect",
    )

    with pytest.raises(RuntimeError, match="^Agent CA material is required$"):
        ensure_agent_ca(settings)


def test_ensure_agent_ca_rejects_mismatched_key_and_certificate(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)
    unrelated_key = ed25519.Ed25519PrivateKey.generate()
    settings.agent_ca_key_path.write_bytes(
        unrelated_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    with pytest.raises(AgentIdentityError, match="^Agent CA material is invalid$"):
        ensure_agent_ca(settings)


def test_issue_agent_certificate_rejects_malformed_csr(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)

    with pytest.raises(AgentIdentityError, match="^Certificate request is invalid$"):
        issue_agent_certificate("not a CSR", "node-123", settings)


def test_issue_agent_certificate_rejects_invalid_csr_signature(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)
    _, csr_pem = _csr()
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    csr_der = bytearray(csr.public_bytes(serialization.Encoding.DER))
    csr_der[-1] ^= 1
    invalid_csr_pem = (
        b"-----BEGIN CERTIFICATE REQUEST-----\n"
        + base64.encodebytes(csr_der)
        + b"-----END CERTIFICATE REQUEST-----\n"
    ).decode()

    with pytest.raises(AgentIdentityError, match="^Certificate request is invalid$"):
        issue_agent_certificate(invalid_csr_pem, "node-123", settings)


def test_verify_agent_signature_rejects_certificate_from_wrong_signer(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)
    key = ed25519.Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    certificate_pem = _certificate(
        settings,
        key,
        now=now,
        signer=ed25519.Ed25519PrivateKey.generate(),
    )

    with pytest.raises(AgentIdentityError, match="^Agent identity verification failed$"):
        verify_agent_signature(certificate_pem, b"nonce", key.sign(b"nonce"), settings, now=now)


@pytest.mark.parametrize(
    ("not_before_delta", "not_after_delta"),
    [
        (timedelta(days=-2), timedelta(days=-1)),
        (timedelta(days=1), timedelta(days=2)),
    ],
    ids=["expired", "not-yet-valid"],
)
def test_verify_agent_signature_rejects_certificate_outside_validity_window(
    tmp_path: Path,
    not_before_delta: timedelta,
    not_after_delta: timedelta,
) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)
    key = ed25519.Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    certificate_pem = _certificate(
        settings,
        key,
        now=now,
        not_before=now + not_before_delta,
        not_after=now + not_after_delta,
    )

    with pytest.raises(AgentIdentityError, match="^Agent identity verification failed$"):
        verify_agent_signature(certificate_pem, b"nonce", key.sign(b"nonce"), settings, now=now)


def test_verify_agent_signature_rejects_wrong_eku(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)
    key = ed25519.Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    certificate_pem = _certificate(settings, key, now=now, eku=ExtendedKeyUsageOID.SERVER_AUTH)

    with pytest.raises(AgentIdentityError, match="^Agent identity verification failed$"):
        verify_agent_signature(certificate_pem, b"nonce", key.sign(b"nonce"), settings, now=now)


@pytest.mark.parametrize("common_name", [None, " "], ids=["missing", "empty"])
def test_verify_agent_signature_rejects_empty_common_name(tmp_path: Path, common_name: str | None) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)
    key = ed25519.Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    certificate_pem = _certificate(settings, key, now=now, common_name=common_name)

    with pytest.raises(AgentIdentityError, match="^Agent identity verification failed$"):
        verify_agent_signature(certificate_pem, b"nonce", key.sign(b"nonce"), settings, now=now)


def test_verify_agent_signature_rejects_invalid_nonce_signature(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_agent_ca(settings)
    key = ed25519.Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    certificate_pem = _certificate(settings, key, now=now)

    with pytest.raises(AgentIdentityError, match="^Agent identity verification failed$"):
        verify_agent_signature(certificate_pem, b"nonce", key.sign(b"different"), settings, now=now)


def test_agent_gateway_settings_default_disabled_and_accept_local_ws() -> None:
    settings = Settings(_env_file=None)

    assert settings.agent_gateway_enabled is False
    assert settings.agent_public_ws_url == "ws://127.0.0.1:8000/agent/v1/connect"


def test_agent_gateway_settings_require_wss_outside_local() -> None:
    settings = Settings(
        _env_file=None,
        VISIOX_ENV="production",
        agent_public_ws_url="wss://visiox.example/agent/v1/connect",
    )

    assert settings.agent_public_ws_url.startswith("wss://")


def test_agent_gateway_settings_secure_the_untouched_default_outside_local() -> None:
    settings = Settings(_env_file=None, VISIOX_ENV="test")

    assert settings.agent_public_ws_url == "wss://127.0.0.1:8000/agent/v1/connect"


@pytest.mark.parametrize(
    ("environment", "url"),
    [
        ("production", "ws://visiox.example/agent/v1/connect"),
        ("test", "ws://visiox.example/agent/v1/connect"),
        ("local", "http://127.0.0.1:8000/agent/v1/connect"),
        ("local", "ws://127.0.0.1:8000/wrong"),
        ("local", "ws://127.0.0.1:8000/agent/v1/connect?token=secret"),
    ],
)
def test_agent_gateway_settings_reject_insecure_or_wrong_public_url(environment: str, url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, VISIOX_ENV=environment, agent_public_ws_url=url)
