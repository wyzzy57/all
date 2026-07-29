from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_AUTHORIZATION_RE = re.compile(
    r"(?i)\b((?:proxy[-_])?authorization)(\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,;]+)"
)
_CLI_SECRET_RE = re.compile(
    r"(?i)(--(?:password|passwd|token|api-key|api_key|client-secret|client_secret))"
    r"(?:\s*=\s*|\s+)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SENSITIVE_KEY_FAMILY = (
    r"password|passwd|credentials?|session[_-]?token|token|api[_-]?key|"
    r"client[_-]?secret|secret|private[_-]?key|access[_-]?key|awsaccesskeyid|"
    r"signature|sig|auth|registry[_-]?(?:auth|credentials?|password|token)"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    rf"(?i)(?<![A-Za-z0-9_.-])"
    rf"([A-Za-z0-9_.-]*(?:{_SENSITIVE_KEY_FAMILY}))[\"']?"
    r"(\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s&,;]+)"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_SENSITIVE_CODE_MARKERS = (
    "PASSWORD",
    "PASSWD",
    "TOKEN",
    "SECRET",
    "PRIVATE_KEY",
    "API_KEY",
    "SIGNATURE",
    "CREDENTIAL",
    "AUTHORIZATION",
)

_STRUCTURED_SENSITIVE_KEY_RE = re.compile(
    r"(?i)^(?:password|passwd|credentials?|session[_-]?token|token|api[_-]?key|"
    r"client[_-]?secret|secret|private[_-]?key|access[_-]?key|awsaccesskeyid|"
    r"signature|sig|auth|authorization|proxy[-_]?authorization|cookie|set-cookie|"
    r"registry[_-]?(?:auth|credentials?|password|token))$"
)


def redact(value: object) -> str:
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return "[REDACTED BINARY DATA]"
    else:
        text = str(value)

    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", text)
    text = _AUTHORIZATION_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )
    text = _CLI_SECRET_RE.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    return _CREDENTIAL_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )


def redact_recursive(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _STRUCTURED_SENSITIVE_KEY_RE.fullmatch(str(key))
                else redact_recursive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_recursive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_recursive(item) for item in value)
    if isinstance(value, (str, bytes)):
        return redact(value)
    return value


def redact_uri(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    return redact(sanitized)


def sanitize_error(
    error_code: str | None,
    error_message: str | None,
) -> tuple[str | None, str | None]:
    sanitized_code: str | None = None
    if error_code is not None:
        normalized = "".join(
            character if character.isalnum() or character == "_" else "_"
            for character in error_code.upper()
        )
        if any(marker in normalized for marker in _SENSITIVE_CODE_MARKERS):
            sanitized_code = "EDGE_OPERATION_FAILED"
        else:
            sanitized_code = normalized[:80] or "EDGE_OPERATION_FAILED"
    return sanitized_code, redact(error_message) if error_message else None
