from visiox_db.models import Annotation, BaseModel, Dataset, DatasetSample, TrainingPipeline
from visiox_db.models.identity import ResourceGrant


def test_pipeline_ownership_listing_and_action_permissions(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    headers = identity["member_headers"]
    created_response = client.post(
        "/pipelines",
        headers=headers,
        json={"name": "member-pipeline", "task": "detect", "scale": "n"},
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["organization_id"] == identity["organization_id"]
    assert created["owner_user_id"] == identity["member_id"]
    assert created["visibility"] == "private"

    with session_factory() as session:
        private = TrainingPipeline(
            name="private-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        shared = TrainingPipeline(
            name="shared-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        foreign = TrainingPipeline(
            name="foreign-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["other_organization_id"],
            owner_user_id=identity["outsider_id"],
        )
        session.add_all([private, shared, foreign])
        session.flush()
        session.add(
            ResourceGrant(
                organization_id=identity["organization_id"],
                resource_type="pipeline",
                resource_id=shared.id,
                principal_type="user",
                principal_id=identity["member_id"],
                permissions=["view"],
                created_by=identity["admin_id"],
            )
        )
        session.commit()
        private_id = private.id
        shared_id = shared.id
        foreign_id = foreign.id

    listed = client.get("/pipelines", headers=headers)
    assert listed.status_code == 200, listed.text
    assert {item["name"] for item in listed.json()["items"]} == {
        "member-pipeline",
        "shared-pipeline",
    }
    assert client.get(f"/pipelines/{shared_id}", headers=headers).status_code == 200
    assert client.patch(
        f"/pipelines/{shared_id}", headers=headers, json={"name": "forbidden"}
    ).status_code == 403
    assert client.delete(f"/pipelines/{shared_id}", headers=headers).status_code == 403
    assert client.get(f"/pipelines/{private_id}", headers=headers).status_code == 403
    assert client.get(f"/pipelines/{foreign_id}", headers=headers).status_code == 404


def test_pipeline_creation_requires_dataset_use_permission(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    headers = identity["member_headers"]
    with session_factory() as session:
        dataset = Dataset(
            name="training-source",
            task="detect",
            status="validated",
            sample_count=1,
            annotation_count=1,
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        base_model = BaseModel(
            family="yolo26",
            task="detect",
            scale="n",
            filename="permission-yolo26n.pt",
            source_path="ultralytics:yolo26n.pt",
            local_uri="memory://models/yolo26n.pt",
            status="ready",
        )
        session.add_all([dataset, base_model])
        session.flush()
        sample = DatasetSample(dataset_id=dataset.id, file_uri="memory://sample.png")
        session.add(sample)
        session.flush()
        session.add(
            Annotation(
                dataset_sample_id=sample.id,
                source="test",
                validation_status="valid",
            )
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
        session.add(grant)
        session.commit()
        dataset_id = dataset.id
        base_model_id = base_model.id
        grant_id = grant.id

    payload = {
        "name": "use-protected-pipeline",
        "task": "detect",
        "scale": "n",
        "base_model_id": base_model_id,
        "dataset_id": dataset_id,
    }
    denied = client.post("/pipelines", headers=headers, json=payload)
    assert denied.status_code == 403

    with session_factory() as session:
        grant = session.get(ResourceGrant, grant_id)
        assert grant is not None
        grant.permissions = ["view", "use"]
        session.add(grant)
        session.commit()

    allowed = client.post("/pipelines", headers=headers, json=payload)
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["owner_user_id"] == identity["member_id"]
