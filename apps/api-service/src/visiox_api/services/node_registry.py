import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.schemas.agent_protocol import EnrollmentRequest, InventoryMessage
from visiox_api.services.agent_identity import (
    AgentIdentityError,
    IssuedAgentCertificate,
    hash_enrollment_token,
    issue_agent_certificate,
)
from visiox_common.settings import Settings
from visiox_db.models import AgentEnrollmentToken, ComputeNode, ResourcePool


class EnrollmentRejected(Exception):
    """Raised for every invalid or unusable enrollment credential."""


class NodeNotFound(Exception):
    """Raised when a registry operation targets an unknown node."""


@dataclass(frozen=True, slots=True)
class CreatedEnrollmentToken:
    id: str
    name: str
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class EnrollmentResult:
    node: ComputeNode
    certificate: IssuedAgentCertificate


_DEFAULT_POOLS = {
    "jetson": ("jetson-default", "arm64"),
    "x86_nvidia": ("x86-nvidia-default", "amd64"),
}


class NodeRegistryService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def create_enrollment_token(
        self,
        name: str,
        now: datetime | None = None,
    ) -> CreatedEnrollmentToken:
        current_time = _as_utc(now)
        raw_token = secrets.token_urlsafe(32)
        token = AgentEnrollmentToken(
            name=name,
            token_hash=hash_enrollment_token(raw_token),
            expires_at=current_time + timedelta(minutes=self.settings.agent_enrollment_token_ttl_minutes),
        )
        try:
            self.session.add(token)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return CreatedEnrollmentToken(token.id, token.name, raw_token, token.expires_at)

    def enroll(
        self,
        request: EnrollmentRequest,
        now: datetime | None = None,
    ) -> EnrollmentResult:
        current_time = _as_utc(now)
        token_hash = hash_enrollment_token(request.token)
        try:
            token = self.session.scalar(
                select(AgentEnrollmentToken)
                .where(AgentEnrollmentToken.token_hash == token_hash)
                .with_for_update()
            )
            if (
                token is None
                or token.used_at is not None
                or _as_utc(token.expires_at) <= current_time
            ):
                raise EnrollmentRejected("Enrollment rejected")
            if self.session.scalar(select(ComputeNode.id).where(ComputeNode.name == request.node_name)):
                raise EnrollmentRejected("Enrollment rejected")

            pool = self._get_or_create_default_pool(request.platform_kind)
            node = ComputeNode(
                name=request.node_name,
                resource_pool_id=pool.id,
                status="enrolling",
                architecture=request.architecture,
                platform_kind=request.platform_kind,
                capabilities={},
                resources={},
                fingerprint={},
                agent_version=request.agent_version,
            )
            self.session.add(node)
            self.session.flush()

            certificate = issue_agent_certificate(
                request.csr_pem,
                node.id,
                self.settings,
                now=current_time,
            )
            node.certificate_serial = certificate.serial_number
            node.certificate_fingerprint = certificate.fingerprint_sha256
            node.certificate_expires_at = certificate.expires_at
            token.used_at = current_time
            token.node_id = node.id
            self.session.commit()
            return EnrollmentResult(node=node, certificate=certificate)
        except IntegrityError:
            self.session.rollback()
            raise EnrollmentRejected("Enrollment rejected") from None
        except (EnrollmentRejected, AgentIdentityError):
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

    def apply_inventory(
        self,
        node_id: str,
        inventory: InventoryMessage,
        now: datetime | None = None,
    ) -> ComputeNode:
        current_time = _as_utc(now)
        try:
            node = self._get_node_for_update(node_id)
            node.capabilities = dict(inventory.capabilities)
            node.resources = dict(inventory.resources)
            node.agent_version = inventory.agent_version
            node.last_seen_at = current_time

            matches_enrollment = (
                node.architecture == inventory.architecture
                and node.platform_kind == inventory.platform_kind
            )
            fingerprint = dict(inventory.fingerprint)
            if not matches_enrollment:
                fingerprint["compatibility_error"] = "reported inventory does not match enrollment declaration"
                node.status = "incompatible"
            else:
                fingerprint.pop("compatibility_error", None)
                pool = self._get_or_create_default_pool(inventory.platform_kind)
                node.resource_pool_id = pool.id
                if node.status not in {"draining", "disabled"}:
                    node.status = "online"
            node.fingerprint = fingerprint
            self.session.commit()
            return node
        except Exception:
            self.session.rollback()
            raise

    def mark_seen(self, node_id: str, now: datetime | None = None) -> ComputeNode:
        current_time = _as_utc(now)
        try:
            node = self._get_node_for_update(node_id)
            node.last_seen_at = current_time
            if node.status in {"enrolling", "offline", "online"}:
                node.status = "online"
            self.session.commit()
            return node
        except Exception:
            self.session.rollback()
            raise

    def _get_node_for_update(self, node_id: str) -> ComputeNode:
        node = self.session.scalar(
            select(ComputeNode).where(ComputeNode.id == node_id).with_for_update()
        )
        if node is None:
            raise NodeNotFound(node_id)
        return node

    def _get_or_create_default_pool(self, platform_kind: str) -> ResourcePool:
        pool_name, architecture = _DEFAULT_POOLS[platform_kind]
        pool = self.session.scalar(select(ResourcePool).where(ResourcePool.name == pool_name))
        if pool is not None:
            return pool
        pool = ResourcePool(
            name=pool_name,
            kind=platform_kind,
            selector={"architecture": architecture, "platform_kind": platform_kind},
            compatibility_policy={"architecture": architecture, "platform_kind": platform_kind},
            enabled=True,
        )
        self.session.add(pool)
        self.session.flush()
        return pool


def refresh_stale_nodes(session: Session, now: datetime, offline_after_seconds: int) -> None:
    threshold = _as_utc(now) - timedelta(seconds=offline_after_seconds)
    result = session.execute(
        update(ComputeNode)
        .where(
            ComputeNode.status == "online",
            ComputeNode.last_seen_at.is_not(None),
            ComputeNode.last_seen_at < threshold,
        )
        .values(status="offline")
    )
    if result.rowcount:
        session.commit()


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
