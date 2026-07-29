from datetime import UTC, datetime, timedelta

import pytest

from visiox_api.services.audit import REDACTED, record_audit
from visiox_db.models.identity import AUDIT_RESULT_SUCCESS, AuditLog, User


def test_audit_log_filters_are_server_side_scoped_and_redacted(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    start = datetime(2026, 7, 28, 8, tzinfo=UTC)
    with session_factory() as session:
        actor = session.get(User, identity["admin_id"])
        assert actor is not None
        first = record_audit(
            session,
            actor,
            "service.stop",
            "service",
            "service-1",
            AUDIT_RESULT_SUCCESS,
            "request-1",
            {
                "Credential": "credential-value",
                "api-key": "api-key-value",
                "ACCESS_KEY": "access-key-value",
                "Private_Key": "private-key-value",
                "token": "token-value",
                "password": "password-value",
                "cookie": "cookie-value",
                "Authorization": "authorization-value",
                "reason": "operator request",
            },
        )
        first.created_at = start
        second = AuditLog(
            organization_id=identity["organization_id"],
            actor_user_id=identity["member_id"],
            action="dataset.share",
            resource_type="dataset",
            resource_id="dataset-1",
            result="denied",
            request_id="request-2",
            metadata_json={"reason": "forbidden"},
            created_at=start + timedelta(hours=1),
        )
        outsider = AuditLog(
            organization_id=identity["other_organization_id"],
            actor_user_id=identity["outsider_id"],
            action="service.stop",
            resource_type="service",
            resource_id="service-other",
            result=AUDIT_RESULT_SUCCESS,
            metadata_json={},
            created_at=start,
        )
        session.add_all([second, outsider])
        session.commit()
        session.refresh(first)
        assert all(
            value == REDACTED
            for key, value in first.metadata_json.items()
            if key != "reason"
        )

    response = client.get(
        "/admin/audit-logs",
        params={
            "actor_user_id": identity["admin_id"],
            "resource_type": "service",
            "resource_id": "service-1",
            "action": "service.stop",
            "result": AUDIT_RESULT_SUCCESS,
            "created_from": (start - timedelta(minutes=1)).isoformat(),
            "created_to": (start + timedelta(minutes=1)).isoformat(),
        },
        headers=identity["admin_headers"],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert [item["resource_id"] for item in payload["items"]] == ["service-1"]
    assert payload["items"][0]["metadata_json"]["Credential"] == REDACTED
    assert "credential-value" not in response.text


def test_audit_log_response_redacts_nested_legacy_metadata_without_mutating_db(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    original_metadata = {
        "credential": "credential-value",
        "nested": {
            "api-key": "api-key-value",
            "items": [
                {"ACCESS_KEY": "access-key-value"},
                {
                    "Private_Key": "private-key-value",
                    "token": "token-value",
                    "password": "password-value",
                    "cookie": "cookie-value",
                    "Authorization": "authorization-value",
                },
            ],
        },
        "safe": "visible-value",
    }
    with session_factory() as session:
        legacy = AuditLog(
            organization_id=identity["organization_id"],
            actor_user_id=identity["admin_id"],
            action="legacy.import",
            resource_type="dataset",
            resource_id="legacy-dataset",
            result=AUDIT_RESULT_SUCCESS,
            metadata_json=original_metadata,
        )
        session.add(legacy)
        session.commit()
        legacy_id = legacy.id

    response = client.get(
        "/admin/audit-logs",
        params={"action": "legacy.import"},
        headers=identity["admin_headers"],
    )

    assert response.status_code == 200, response.text
    metadata = response.json()["items"][0]["metadata_json"]
    assert metadata == {
        "credential": REDACTED,
        "nested": {
            "api-key": REDACTED,
            "items": [
                {"ACCESS_KEY": REDACTED},
                {
                    "Private_Key": REDACTED,
                    "token": REDACTED,
                    "password": REDACTED,
                    "cookie": REDACTED,
                    "Authorization": REDACTED,
                },
            ],
        },
        "safe": "visible-value",
    }
    sensitive_values = {
        "credential-value",
        "api-key-value",
        "access-key-value",
        "private-key-value",
        "token-value",
        "password-value",
        "cookie-value",
        "authorization-value",
    }
    assert all(value not in response.text for value in sensitive_values)

    with session_factory() as session:
        stored = session.get(AuditLog, legacy_id)
        assert stored is not None
        assert stored.metadata_json == original_metadata


def test_audit_log_filters_accept_mixed_naive_and_aware_time_range(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    created_at = datetime(2026, 7, 28, 8, tzinfo=UTC)
    with session_factory() as session:
        session.add(
            AuditLog(
                organization_id=identity["organization_id"],
                actor_user_id=identity["admin_id"],
                action="mixed-timezone.query",
                result=AUDIT_RESULT_SUCCESS,
                metadata_json={},
                created_at=created_at,
            )
        )
        session.commit()

    response = client.get(
        "/admin/audit-logs",
        params={
            "action": "mixed-timezone.query",
            "created_from": "2026-07-28T07:59:00",
            "created_to": "2026-07-28T16:01:00+08:00",
        },
        headers=identity["admin_headers"],
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


@pytest.mark.parametrize(
    ("query_key", "model_field", "matching_value", "other_value"),
    [
        ("actor_user_id", "actor_user_id", "admin_id", "member_id"),
        (
            "resource_type",
            "resource_type",
            "filter-resource-type-target",
            "filter-resource-type-other",
        ),
        (
            "resource_id",
            "resource_id",
            "filter-resource-id-target",
            "filter-resource-id-other",
        ),
        ("action", "action", "filter.action.target", "filter.action.other"),
        ("result", "result", "filter-result-target", "filter-result-other"),
    ],
)
def test_audit_log_filters_independently_exclude_non_matching_records(
    authenticated_client,
    query_key: str,
    model_field: str,
    matching_value: str,
    other_value: str,
) -> None:
    client, session_factory, _, identity = authenticated_client
    target = identity.get(matching_value, matching_value)
    other = identity.get(other_value, other_value)
    common = {
        "organization_id": identity["organization_id"],
        "actor_user_id": identity["admin_id"],
        "action": "filter.default",
        "resource_type": "filter-default-type",
        "resource_id": "filter-default-id",
        "result": AUDIT_RESULT_SUCCESS,
        "metadata_json": {},
    }
    with session_factory() as session:
        matching = AuditLog(**(common | {model_field: target}))
        non_matching = AuditLog(**(common | {model_field: other}))
        session.add_all([matching, non_matching])
        session.commit()
        matching_id = matching.id
        non_matching_id = non_matching.id

    response = client.get(
        "/admin/audit-logs",
        params={query_key: target},
        headers=identity["admin_headers"],
    )

    assert response.status_code == 200, response.text
    returned_ids = {item["id"] for item in response.json()["items"]}
    assert matching_id in returned_ids
    assert non_matching_id not in returned_ids


def test_audit_log_time_filters_use_inclusive_start_and_exclusive_end(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    minute_start = datetime(2026, 7, 28, 23, 59, tzinfo=UTC)
    with session_factory() as session:
        before = AuditLog(
            organization_id=identity["organization_id"],
            actor_user_id=identity["admin_id"],
            action="time.before",
            result=AUDIT_RESULT_SUCCESS,
            metadata_json={},
            created_at=minute_start - timedelta(seconds=1),
        )
        last_second = AuditLog(
            organization_id=identity["organization_id"],
            actor_user_id=identity["admin_id"],
            action="time.last-second",
            result=AUDIT_RESULT_SUCCESS,
            metadata_json={},
            created_at=minute_start + timedelta(seconds=59),
        )
        next_minute = AuditLog(
            organization_id=identity["organization_id"],
            actor_user_id=identity["admin_id"],
            action="time.next-minute",
            result=AUDIT_RESULT_SUCCESS,
            metadata_json={},
            created_at=minute_start + timedelta(minutes=1),
        )
        session.add_all([before, last_second, next_minute])
        session.commit()
        before_id = before.id
        last_second_id = last_second.id
        next_minute_id = next_minute.id

    from_response = client.get(
        "/admin/audit-logs",
        params={"created_from": minute_start.isoformat()},
        headers=identity["admin_headers"],
    )
    to_response = client.get(
        "/admin/audit-logs",
        params={"created_to": (minute_start + timedelta(minutes=1)).isoformat()},
        headers=identity["admin_headers"],
    )

    assert from_response.status_code == 200, from_response.text
    from_ids = {item["id"] for item in from_response.json()["items"]}
    assert before_id not in from_ids
    assert last_second_id in from_ids
    assert next_minute_id in from_ids
    assert to_response.status_code == 200, to_response.text
    to_ids = {item["id"] for item in to_response.json()["items"]}
    assert before_id in to_ids
    assert last_second_id in to_ids
    assert next_minute_id not in to_ids


def test_audit_log_keyset_pages_ignore_backfills_before_the_page_boundary(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    start = datetime(2026, 7, 28, 8, tzinfo=UTC)
    with session_factory() as session:
        original_logs = [
            AuditLog(
                organization_id=identity["organization_id"],
                actor_user_id=identity["admin_id"],
                action="keyset.page",
                result=AUDIT_RESULT_SUCCESS,
                metadata_json={},
                created_at=start + timedelta(seconds=index),
            )
            for index in range(21)
        ]
        session.add_all(original_logs)
        session.commit()
        oldest_id = original_logs[0].id

    first_page = client.get(
        "/admin/audit-logs",
        params={"action": "keyset.page", "limit": 20},
        headers=identity["admin_headers"],
    )

    assert first_page.status_code == 200, first_page.text
    first_payload = first_page.json()
    assert first_payload["total"] == 21
    assert len(first_payload["items"]) == 20
    first_page_ids = {item["id"] for item in first_payload["items"]}
    next_cursor = first_payload["next_cursor"]
    assert next_cursor

    with session_factory() as session:
        session.add(
            AuditLog(
                organization_id=identity["organization_id"],
                actor_user_id=identity["admin_id"],
                action="keyset.page",
                result=AUDIT_RESULT_SUCCESS,
                metadata_json={},
                created_at=start + timedelta(seconds=1, milliseconds=500),
            )
        )
        session.commit()

    second_page = client.get(
        "/admin/audit-logs",
        params={
            "action": "keyset.page",
            "cursor": next_cursor,
            "limit": 20,
        },
        headers=identity["admin_headers"],
    )

    assert second_page.status_code == 200, second_page.text
    second_payload = second_page.json()
    assert first_page_ids.isdisjoint(item["id"] for item in second_payload["items"])
    assert [item["id"] for item in second_payload["items"]] == [oldest_id]
    assert second_payload["next_cursor"] is None


def test_audit_log_cursor_rejects_malformed_values(authenticated_client) -> None:
    client, _, _, identity = authenticated_client

    for malformed_cursor in ("not-base64!", "e30"):
        response = client.get(
            "/admin/audit-logs",
            params={"cursor": malformed_cursor},
            headers=identity["admin_headers"],
        )

        assert response.status_code == 422, response.text


def test_audit_log_filters_reject_invalid_time_range_and_members(authenticated_client) -> None:
    client, _, _, identity = authenticated_client
    invalid = client.get(
        "/admin/audit-logs",
        params={
            "created_from": "2026-07-29T00:00:00Z",
            "created_to": "2026-07-28T00:00:00Z",
        },
        headers=identity["admin_headers"],
    )
    denied = client.get("/admin/audit-logs", headers=identity["member_headers"])

    assert invalid.status_code == 422
    assert denied.status_code == 403
