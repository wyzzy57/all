from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from visiox_db.models import AuditLog, ComputeNode, Dataset, ResourceGrant, UserGroup


def test_owner_replaces_resource_sharing_and_visibility(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        dataset = Dataset(
            name="shared-dataset",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["member_id"],
        )
        group = UserGroup(
            organization_id=identity["organization_id"],
            name="Reviewers",
        )
        session.add_all([dataset, group]); session.commit()
        dataset_id, group_id = dataset.id, group.id

    response = client.put(
        f"/resources/dataset/{dataset_id}/sharing",
        headers=identity["member_headers"],
        json={
            "grants": [
                {
                    "principal_type": "group",
                    "principal_id": group_id,
                    "permissions": ["view", "use"],
                },
                {
                    "principal_type": "organization",
                    "principal_id": identity["organization_id"],
                    "permissions": ["view"],
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["visibility"] == "organization"
    assert len(response.json()["grants"]) == 2
    listing = client.get(
        f"/resources/dataset/{dataset_id}/sharing",
        headers=identity["member_headers"],
    )
    assert listing.status_code == 200
    with session_factory() as session:
        dataset = session.get(Dataset, dataset_id)
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "resource.sharing.replace",
                AuditLog.resource_id == dataset_id,
            )
        )
        assert dataset is not None and dataset.visibility == "organization"
        assert audit is not None

    replacement = client.put(
        f"/resources/dataset/{dataset_id}/sharing",
        headers=identity["member_headers"],
        json={"grants": []},
    )
    assert replacement.status_code == 200
    assert replacement.json() == {
        "resource_type": "dataset",
        "resource_id": dataset_id,
        "visibility": "private",
        "grants": [],
    }


def test_non_owner_cannot_manage_sharing(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        dataset = Dataset(
            name="admin-dataset",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        session.add(dataset); session.commit()
        dataset_id = dataset.id

    response = client.put(
        f"/resources/dataset/{dataset_id}/sharing",
        headers=identity["member_headers"],
        json={"grants": []},
    )
    assert response.status_code == 403


def test_invalid_replacement_does_not_delete_existing_grants(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        dataset = Dataset(
            name="atomic-dataset",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        session.add(dataset); session.flush()
        session.add(
            ResourceGrant(
                organization_id=identity["organization_id"],
                resource_type="dataset",
                resource_id=dataset.id,
                principal_type="user",
                principal_id=identity["member_id"],
                permissions=["view"],
                created_by=identity["admin_id"],
            )
        )
        session.commit(); dataset_id = dataset.id

    response = client.put(
        f"/resources/dataset/{dataset_id}/sharing",
        headers=identity["admin_headers"],
        json={
            "grants": [{
                "principal_type": "user",
                "principal_id": identity["outsider_id"],
                "permissions": ["view"],
            }]
        },
    )
    assert response.status_code == 422
    with session_factory() as session:
        grants = session.scalars(
            select(ResourceGrant).where(ResourceGrant.resource_id == dataset_id)
        ).all()
        assert [(grant.principal_id, grant.permissions) for grant in grants] == [
            (identity["member_id"], ["view"])
        ]


def test_node_cannot_be_published_to_organization(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        node = ComputeNode(
            name="private-node",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            architecture="x86_64",
            platform_kind="linux",
            agent_version="manual-ssh",
        )
        session.add(node); session.commit(); node_id = node.id

    response = client.put(
        f"/resources/node/{node_id}/sharing",
        headers=identity["admin_headers"],
        json={
            "grants": [{
                "principal_type": "organization",
                "principal_id": identity["organization_id"],
                "permissions": ["view"],
            }]
        },
    )
    assert response.status_code == 422


def test_sharing_rejects_unsupported_permission_and_expired_grant(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        dataset = Dataset(
            name="validation-dataset",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        session.add(dataset); session.commit(); dataset_id = dataset.id

    for grant in (
        {
            "principal_type": "user",
            "principal_id": identity["member_id"],
            "permissions": ["invoke"],
        },
        {
            "principal_type": "user",
            "principal_id": identity["member_id"],
            "permissions": ["view"],
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    ):
        response = client.put(
            f"/resources/dataset/{dataset_id}/sharing",
            headers=identity["admin_headers"],
            json={"grants": [grant]},
        )
        assert response.status_code == 422, response.text


def test_members_can_list_same_organization_sharing_principals(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        session.add(
            UserGroup(
                organization_id=identity["organization_id"],
                name="Operators",
            )
        )
        session.commit()

    response = client.get(
        "/resources/sharing-principals",
        headers=identity["member_headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["username"] for item in body["users"]} == {"active-user", "member"}
    assert [item["name"] for item in body["groups"]] == ["Operators"]
    assert "password" not in response.text
    assert "outsider" not in response.text
