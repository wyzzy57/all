from visiox_edge_executor_worker.redaction import redact, redact_uri


def test_redactor_removes_credentials_and_signed_urls() -> None:
    value = redact(
        "Authorization: Bearer secret password=secret "
        "https://storage.test/model?X-Amz-Signature=secret&X-Amz-Credential=secret"
    )

    assert "secret" not in value.casefold()
    assert "authorization: bearer [redacted]" in value.casefold()


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
