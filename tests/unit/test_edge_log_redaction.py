import pytest

from visiox_edge_executor_worker.redaction import redact, redact_uri, sanitize_error


def test_redactor_removes_credentials_and_signed_urls() -> None:
    value = redact(
        "Authorization: Bearer secret password=secret "
        "https://storage.test/model?X-Amz-Signature=secret&X-Amz-Credential=secret"
    )

    assert "secret" not in value.casefold()
    assert "authorization: [redacted]" in value.casefold()


def test_redactor_removes_private_keys_and_url_userinfo() -> None:
    value = redact(
        "registry=https://user:password@registry.test/v2 "
        "-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-material\n-----END OPENSSH PRIVATE KEY-----"
    )

    assert "password" not in value
    assert "private-material" not in value
    assert "user:" not in value


def test_redacted_log_uri_drops_query_fragment_and_userinfo() -> None:
    assert (
        redact_uri("https://user:password@logs.test/run.log?token=secret#private")
        == "https://logs.test/run.log"
    )


def test_redactor_handles_bytes_without_echoing_binary_data() -> None:
    assert redact(b"password=secret\xff") == "[REDACTED BINARY DATA]"


@pytest.mark.parametrize(
    ("value", "sensitive"),
    [
        ("DB_PASSWORD=db-secret", "db-secret"),
        ("credentials=plural-secret", "plural-secret"),
        ("credential: singular-secret", "singular-secret"),
        ("SESSION_TOKEN=session-secret", "session-secret"),
        ("X-Goog-Signature=goog-secret", "goog-secret"),
        ("CloudFront-Signature: cloudfront-secret", "cloudfront-secret"),
        ("Authorization: Token token-secret", "token-secret"),
        ("Authorization=Negotiate negotiate-secret", "negotiate-secret"),
        ("Authorization: CustomScheme custom-secret", "custom-secret"),
        ("Proxy-Authorization: Basic basic-secret", "basic-secret"),
        ("X-Api-Key: api-secret", "api-secret"),
        ("api_key=api-secret", "api-secret"),
        ("client_secret: client-secret", "client-secret"),
        ("deploy --password cli-secret", "cli-secret"),
        ("deploy --password=cli-secret", "cli-secret"),
        ("deploy --token cli-secret", "cli-secret"),
        ("https://blob.test/model?sig=azure-secret&se=2099", "azure-secret"),
        ("https://s3.test/x?X-Amz-Security-Token=aws-secret", "aws-secret"),
        ("redis://user:uri-secret@cache.test/0", "uri-secret"),
        ("registry_password=registry-secret", "registry-secret"),
        ('{"auths":{"registry.test":{"auth":"registry-auth-secret"}}}', "registry-auth-secret"),
        ('{"username":"robot","password":"json-secret"}', "json-secret"),
        ("PRIVATE-TOKEN: git-secret", "git-secret"),
    ],
)
def test_redactor_blocks_common_credential_bypasses(value: str, sensitive: str) -> None:
    assert sensitive.casefold() not in redact(value).casefold()


@pytest.mark.parametrize(
    "value",
    [
        "password_policy=strict",
        "token_count=4",
        "signature_algorithm=sha256",
        "credential_rotation_enabled=true",
        "authorization_timeout=5",
        "X-Amz-Date=20260720T000000Z",
        "X-Amz-Algorithm=AWS4-HMAC-SHA256",
        "https://registry.test/v2/catalog",
        "passwordless authentication is enabled",
    ],
)
def test_redactor_preserves_benign_security_related_text(value: str) -> None:
    assert redact(value) == value


def test_structured_error_sanitizer_replaces_sensitive_codes_and_messages() -> None:
    code, message = sanitize_error(
        "client_secret=code-secret",
        "Authorization: Token message-secret",
    )

    assert code == "EDGE_OPERATION_FAILED"
    assert "code-secret" not in code.casefold()
    assert "message-secret" not in message.casefold()
