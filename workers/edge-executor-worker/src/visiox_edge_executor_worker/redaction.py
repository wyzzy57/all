from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_AUTHORIZATION_RE = re.compile(
    r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+[^\s,;]+"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|private[_-]?key|access[_-]?key|"
    r"x-amz-signature|x-amz-credential|x-amz-security-token)"
    r"\s*([=:])\s*(?:\"[^\"]*\"|'[^']*'|[^\s&,;]+)"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_URL_USERINFO_RE = re.compile(r"(?i)\b(https?://)[^/@\s]+@")


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
    text = _AUTHORIZATION_RE.sub("Authorization: Bearer [REDACTED]", text)
    return _CREDENTIAL_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )


def redact_uri(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    return redact(sanitized)
