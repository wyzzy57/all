import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from visiox_common.settings import Settings


class AgentIdentityError(Exception):
    """Raised when agent identity material cannot be trusted."""


@dataclass(frozen=True, slots=True)
class IssuedAgentCertificate:
    certificate_pem: str
    ca_certificate_pem: str
    serial_number: str
    fingerprint_sha256: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedAgentIdentity:
    node_id: str
    serial_number: str
    fingerprint_sha256: str
    public_key_bytes: bytes


def hash_enrollment_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def csr_fingerprint_sha256(csr_pem: str) -> str:
    csr = _load_valid_csr(csr_pem)
    return hashlib.sha256(csr.public_bytes(serialization.Encoding.DER)).hexdigest()


def csr_public_key_bytes(csr_pem: str) -> bytes:
    csr = _load_valid_csr(csr_pem)
    public_key = csr.public_key()
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise AgentIdentityError("Certificate request is invalid")
    return _public_key_bytes(public_key)


def ensure_agent_ca(settings: Settings) -> None:
    cert_exists = settings.agent_ca_cert_path.is_file()
    key_exists = settings.agent_ca_key_path.is_file()
    if cert_exists and key_exists:
        _load_agent_ca(settings)
        return

    if cert_exists or key_exists or settings.environment != "local" or not settings.agent_auto_generate_ca:
        raise RuntimeError("Agent CA material is required")

    _generate_agent_ca(settings)
    _load_agent_ca(settings)


def issue_agent_certificate(
    csr_pem: str,
    node_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> IssuedAgentCertificate:
    current_time = _as_utc(now)
    try:
        csr = _load_valid_csr(csr_pem)
        public_key = csr.public_key()
        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            raise ValueError
        normalized_node_id = node_id.strip()
        if not normalized_node_id:
            raise ValueError
    except Exception:
        raise AgentIdentityError("Certificate request is invalid") from None

    ca_key, ca_certificate = _load_agent_ca(settings, now=current_time)
    expires_at = min(
        current_time + timedelta(days=settings.agent_certificate_ttl_days),
        ca_certificate.not_valid_after_utc,
    )
    try:
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, normalized_node_id)]))
            .issuer_name(ca_certificate.subject)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(current_time - timedelta(minutes=1))
            .not_valid_after(expires_at)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .sign(ca_key, algorithm=None)
        )
    except Exception:
        raise AgentIdentityError("Certificate request is invalid") from None

    return IssuedAgentCertificate(
        certificate_pem=certificate.public_bytes(serialization.Encoding.PEM).decode(),
        ca_certificate_pem=ca_certificate.public_bytes(serialization.Encoding.PEM).decode(),
        serial_number=str(certificate.serial_number),
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
        expires_at=expires_at,
    )


def verify_agent_certificate(
    certificate_pem: str,
    settings: Settings,
    now: datetime | None = None,
) -> VerifiedAgentIdentity:
    identity, _ = _verify_agent_certificate(certificate_pem, settings, now)
    return identity


def verify_agent_signature(
    certificate_pem: str,
    nonce: bytes,
    signature: bytes,
    settings: Settings,
    now: datetime | None = None,
) -> VerifiedAgentIdentity:
    try:
        identity, agent_public_key = _verify_agent_certificate(certificate_pem, settings, now)
        agent_public_key.verify(signature, nonce)
    except Exception:
        raise AgentIdentityError("Agent identity verification failed") from None

    return identity


def _verify_agent_certificate(
    certificate_pem: str,
    settings: Settings,
    now: datetime | None,
) -> tuple[VerifiedAgentIdentity, ed25519.Ed25519PublicKey]:
    current_time = _as_utc(now)
    try:
        certificate = x509.load_pem_x509_certificate(certificate_pem.encode())
        _, ca_certificate = _load_agent_ca(settings, now=current_time)
        ca_public_key = ca_certificate.public_key()
        if not isinstance(ca_public_key, ed25519.Ed25519PublicKey):
            raise ValueError
        if certificate.issuer != ca_certificate.subject:
            raise ValueError
        ca_public_key.verify(certificate.signature, certificate.tbs_certificate_bytes)
        if current_time < certificate.not_valid_before_utc or current_time > certificate.not_valid_after_utc:
            raise ValueError
        basic_constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        if basic_constraints.ca:
            raise ValueError
        extended_key_usage = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if ExtendedKeyUsageOID.CLIENT_AUTH not in extended_key_usage:
            raise ValueError
        common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(common_names) != 1 or not common_names[0].value.strip():
            raise ValueError
        agent_public_key = certificate.public_key()
        if not isinstance(agent_public_key, ed25519.Ed25519PublicKey):
            raise ValueError
    except Exception:
        raise AgentIdentityError("Agent identity verification failed") from None

    return (
        VerifiedAgentIdentity(
            node_id=common_names[0].value,
            serial_number=str(certificate.serial_number),
            fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
            public_key_bytes=_public_key_bytes(agent_public_key),
        ),
        agent_public_key,
    )


def _generate_agent_ca(settings: Settings) -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Visiox Agent CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(key_cert_sign=True, crl_sign=True, digital_signature=False,
                                     content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .sign(private_key, algorithm=None)
    )
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    settings.agent_ca_key_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_ca_cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_fd = os.open(settings.agent_ca_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(key_fd, "wb") as key_file:
        key_file.write(key_pem)
    os.chmod(settings.agent_ca_key_path, 0o600)
    settings.agent_ca_cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _load_valid_csr(csr_pem: str) -> x509.CertificateSigningRequest:
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        if not csr.is_signature_valid:
            raise ValueError
    except Exception:
        raise AgentIdentityError("Certificate request is invalid") from None
    return csr


def _load_agent_ca(
    settings: Settings,
    now: datetime | None = None,
) -> tuple[ed25519.Ed25519PrivateKey, x509.Certificate]:
    try:
        private_key = serialization.load_pem_private_key(
            settings.agent_ca_key_path.read_bytes(),
            password=None,
        )
        certificate = x509.load_pem_x509_certificate(settings.agent_ca_cert_path.read_bytes())
        public_key = certificate.public_key()
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            raise ValueError
        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            raise ValueError
        if certificate.subject != certificate.issuer:
            raise ValueError
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        if not constraints.ca:
            raise ValueError
        public_key.verify(certificate.signature, certificate.tbs_certificate_bytes)
        if _public_key_bytes(private_key.public_key()) != _public_key_bytes(public_key):
            raise ValueError
        current_time = _as_utc(now)
        if current_time < certificate.not_valid_before_utc or current_time > certificate.not_valid_after_utc:
            raise ValueError
    except Exception:
        raise AgentIdentityError("Agent CA material is invalid") from None

    return private_key, certificate


def _public_key_bytes(public_key: ed25519.Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
