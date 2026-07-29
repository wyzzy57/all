from __future__ import annotations

from io import BytesIO

from PIL import Image

from visiox_api.routes import services as services_route
from visiox_api.routes.pipeline_inference import PipelinePredictResponse
from visiox_api.routes.services import get_service_stream_producer
from visiox_db.models import ComputeNode, DeploymentService, ResourceGrant, TrainingPipeline


def _node(*, name: str, organization_id: str, owner_user_id: str) -> ComputeNode:
    return ComputeNode(
        name=name,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        architecture="x86_64",
        platform_kind="x86_nvidia",
        agent_version="ssh-bootstrap",
    )


def test_node_lists_are_filtered_and_management_remains_admin_only(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        private = _node(
            name="private-node",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        shared = _node(
            name="shared-node",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        foreign = _node(
            name="foreign-node",
            organization_id=identity["other_organization_id"],
            owner_user_id=identity["outsider_id"],
        )
        session.add_all([private, shared, foreign])
        session.flush()
        session.add(
            ResourceGrant(
                organization_id=identity["organization_id"],
                resource_type="node",
                resource_id=shared.id,
                principal_type="user",
                principal_id=identity["member_id"],
                permissions=["view"],
                created_by=identity["admin_id"],
            )
        )
        session.commit()
        shared_id = shared.id

    listed = client.get("/nodes", headers=identity["member_headers"])
    assert listed.status_code == 200, listed.text
    assert [item["name"] for item in listed.json()["items"]] == ["shared-node"]
    assert client.get(
        f"/nodes/{shared_id}", headers=identity["member_headers"]
    ).status_code == 200
    assert client.post(
        f"/edge-nodes/{shared_id}/probe", headers=identity["member_headers"]
    ).status_code == 403
    assert client.post(
        f"/edge-nodes/{shared_id}/test-connection",
        headers=identity["member_headers"],
    ).status_code == 403


def test_service_invoke_grant_does_not_allow_management(
    authenticated_client,
    monkeypatch,
) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="service-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
        )
        session.add(pipeline)
        session.flush()
        service = DeploymentService(
            name="shared-inference",
            pipeline_id=pipeline.id,
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            model_name="yolo26n.pt",
            model_weight="best.pt",
            environment="cpu",
            instance_name="shared-inference",
            status="running",
            endpoint="http://edge.test:8080",
        )
        session.add(service)
        session.flush()
        session.add(
            ResourceGrant(
                organization_id=identity["organization_id"],
                resource_type="service",
                resource_id=service.id,
                principal_type="user",
                principal_id=identity["member_id"],
                permissions=["view", "invoke"],
                created_by=identity["admin_id"],
            )
        )
        session.commit()
        service_id = service.id

    async def fake_prediction(service, file):
        return PipelinePredictResponse(
            pipeline_id=service.pipeline_id,
            model_weight=service.model_weight,
            environment=service.environment,
            predictions=[],
            result_image="data:image/png;base64,",
            latency_ms=1.0,
        )

    monkeypatch.setattr(services_route, "_request_deployed_prediction", fake_prediction)
    client.app.dependency_overrides[get_service_stream_producer] = lambda: object()
    image = Image.new("RGB", (8, 8), "white")
    payload = BytesIO()
    image.save(payload, format="PNG")

    predicted = client.post(
        f"/services/{service_id}/predict/image",
        headers=identity["member_headers"],
        files={"file": ("sample.png", payload.getvalue(), "image/png")},
    )
    assert predicted.status_code == 200, predicted.text
    assert client.post(
        f"/services/{service_id}/stop", headers=identity["member_headers"]
    ).status_code == 403
    assert client.delete(
        f"/services/{service_id}", headers=identity["member_headers"]
    ).status_code == 403
