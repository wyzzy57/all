from sqlalchemy import select

from visiox_db.models import Dataset, DatasetSample, LabelProject
from visiox_db.models.identity import ResourceGrant


def test_member_dataset_creation_and_authorized_listing(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    headers = identity["member_headers"]

    created_response = client.post(
        "/datasets",
        headers=headers,
        json={"name": "member-owned", "task": "detect", "class_schema": {}},
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["organization_id"] == identity["organization_id"]
    assert created["owner_user_id"] == identity["member_id"]
    assert created["visibility"] == "private"

    with session_factory() as session:
        private = Dataset(
            name="private-other",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        granted = Dataset(
            name="granted-view",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        foreign = Dataset(
            name="foreign",
            task="detect",
            organization_id=identity["other_organization_id"],
            owner_user_id=identity["outsider_id"],
        )
        session.add_all([private, granted, foreign])
        session.flush()
        session.add(
            ResourceGrant(
                organization_id=identity["organization_id"],
                resource_type="dataset",
                resource_id=granted.id,
                principal_type="user",
                principal_id=identity["member_id"],
                permissions=["view"],
                created_by=identity["admin_id"],
            )
        )
        session.commit()
        private_id = private.id
        granted_id = granted.id
        foreign_id = foreign.id

    list_response = client.get("/datasets", headers=headers)
    assert list_response.status_code == 200, list_response.text
    assert {item["name"] for item in list_response.json()["items"]} == {
        "member-owned",
        "granted-view",
    }

    assert client.get(f"/datasets/{granted_id}", headers=headers).status_code == 200
    assert client.delete(f"/datasets/{granted_id}", headers=headers).status_code == 403
    assert client.get(f"/datasets/{private_id}", headers=headers).status_code == 403
    assert client.get(f"/datasets/{foreign_id}", headers=headers).status_code == 404

    with session_factory() as session:
        assert session.scalar(select(Dataset).where(Dataset.id == granted_id)) is not None


def test_dataset_children_inherit_view_and_edit_permissions(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    headers = identity["member_headers"]
    with session_factory() as session:
        dataset = Dataset(
            name="shared-children",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        session.add(dataset)
        session.flush()
        sample = DatasetSample(
            dataset_id=dataset.id,
            file_uri="memory://datasets/sample.png",
            split="unassigned",
        )
        project = LabelProject(
            dataset_id=dataset.id,
            provider="label_studio",
            external_project_id="100",
        )
        grant = ResourceGrant(
            organization_id=identity["organization_id"],
            resource_type="dataset",
            resource_id=dataset.id,
            principal_type="user",
            principal_id=identity["member_id"],
            permissions=["view"],
            created_by=identity["admin_id"],
        )
        session.add_all([sample, project, grant])
        session.commit()
        dataset_id = dataset.id
        sample_id = sample.id
        grant_id = grant.id

    samples = client.get(f"/datasets/{dataset_id}/samples", headers=headers)
    projects = client.get(f"/datasets/{dataset_id}/label-projects", headers=headers)
    denied = client.post(
        f"/datasets/{dataset_id}/samples/splits",
        headers=headers,
        json={"assignments": [{"sample_id": sample_id, "split": "train"}]},
    )
    assert samples.status_code == 200, samples.text
    assert projects.status_code == 200, projects.text
    assert denied.status_code == 403

    with session_factory() as session:
        grant = session.get(ResourceGrant, grant_id)
        assert grant is not None
        grant.permissions = ["view", "edit"]
        session.add(grant)
        session.commit()

    allowed = client.post(
        f"/datasets/{dataset_id}/samples/splits",
        headers=headers,
        json={"assignments": [{"sample_id": sample_id, "split": "train"}]},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["items"][0]["split"] == "train"
