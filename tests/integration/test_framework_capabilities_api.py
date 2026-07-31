def test_authenticated_user_receives_stable_framework_catalog(
    authenticated_client,
) -> None:
    client, _, _, identity = authenticated_client

    first = client.get("/frameworks/capabilities", headers=identity["member_headers"])
    second = client.get("/frameworks/capabilities", headers=identity["member_headers"])

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["task_kind"] is None
    adapters = first.json()["adapters"]
    assert [adapter["adapter_key"] for adapter in adapters] == [
        "llamafactory.llm_sft.v1",
        "paddlex.object_detection.v1",
        "ultralytics.object_detection.v1",
    ]
    assert all(adapter["available"] is False for adapter in adapters)
    assert "visiox123" not in first.text


def test_framework_catalog_filters_task_compatibility(authenticated_client) -> None:
    client, _, _, identity = authenticated_client

    detection = client.get(
        "/frameworks/capabilities?task_kind=object_detection",
        headers=identity["member_headers"],
    )
    llm = client.get(
        "/frameworks/capabilities?task_kind=llm_sft",
        headers=identity["member_headers"],
    )
    unsupported = client.get(
        "/frameworks/capabilities?task_kind=unsupported",
        headers=identity["member_headers"],
    )

    assert detection.status_code == llm.status_code == unsupported.status_code == 200
    assert [item["framework"] for item in detection.json()["adapters"]] == [
        "paddlex",
        "ultralytics",
    ]
    assert [item["framework"] for item in llm.json()["adapters"]] == ["llamafactory"]
    assert unsupported.json() == {"task_kind": "unsupported", "adapters": []}


def test_framework_catalog_rejects_anonymous_request(authenticated_client) -> None:
    client, _, _, _ = authenticated_client

    response = client.get("/frameworks/capabilities")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid authentication credentials"
