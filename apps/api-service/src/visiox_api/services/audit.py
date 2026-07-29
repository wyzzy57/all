from typing import Any

from sqlalchemy.orm import Session

from visiox_db.models.identity import AuditLog, User


REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = {
    "accesskey",
    "apikey",
    "connectionstring",
    "credential",
    "databaseuri",
    "databaseurl",
    "dburi",
    "dburl",
    "passphrase",
    "password",
    "sqlalchemyurl",
    "token",
    "secret",
    "cookie",
    "privatekey",
    "authorization",
}
_SENSITIVE_CONTAINER_KEYS = {"credentials"}


def _normalize_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized_key = _normalize_key(key)
    return normalized_key.endswith("dsn") or any(
        part in normalized_key for part in _SENSITIVE_KEY_PARTS
    )


def _is_sensitive_container(key: object, value: Any) -> bool:
    return (
        isinstance(key, str)
        and isinstance(value, dict)
        and _normalize_key(key) in _SENSITIVE_CONTAINER_KEYS
    )


def redact_audit_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                redact_audit_metadata(item)
                if _is_sensitive_container(key, item) or not _is_sensitive_key(key)
                else REDACTED
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_audit_metadata(item) for item in value]
    return value


def record_audit(
    session: Session,
    actor: User,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    result: str,
    request_id: str | None,
    metadata: dict[str, Any],
) -> AuditLog:
    audit_log = AuditLog(
        organization_id=actor.organization_id,
        actor_user_id=actor.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        request_id=request_id,
        metadata_json=redact_audit_metadata(metadata),
    )
    session.add(audit_log)
    return audit_log
