import json
from pathlib import Path

from sqlalchemy import select

from visiox_api.routes.nodes import get_edge_inventory_service
from visiox_db.models import ComputeNode, ResourcePool
from visiox_edge_executor_worker.inventory import parse_inventory


FIXTURES = Path(__file__).parents[1] / "fixtures" / "edge_inventory"


def _inventory(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ProbeService:
    def __init__(self, inventory: dict[str, object]) -> None:
        self.inventory = parse_inventory(inventory).model_dump(mode="json")
        self.node_ids: list[str] = []

    def probe(self, *, node_id: str, deadline: float | None = None) -> dict[str, object]:
        assert deadline is not None
        self.node_ids.append(node_id)
        return {
            "status": "ok",
            "node_id": node_id,
            "inventory": self.inventory,
        }


def _seed_node(agent_session_factory, *, name: str = "ssh-gpu") -> str:
    with agent_session_factory() as session:
        node = ComputeNode(
            name=name,
            status="online",
            architecture="unknown",
            platform_kind="ssh_edge",
            capabilities={},
            resources={},
            fingerprint={
                "ssh_host_key_type": "ssh-ed25519",
                "ssh_host_key_fingerprint": "SHA256:fixture",
            },
            agent_version="ssh-bootstrap",
        )
        session.add(node)
        session.commit()
        return node.id


def test_probe_persists_inventory_and_assigns_exact_compatible_pool(
    agent_api_client,
    agent_session_factory,
):
    node_id = _seed_node(agent_session_factory)
    service = ProbeService(_inventory("x86.json"))
    agent_api_client.app.dependency_overrides[get_edge_inventory_service] = lambda: service

    response = agent_api_client.post(f"/edge-nodes/{node_id}/probe")

    assert response.status_code == 200
    body = response.json()
    assert body["supported"] is True
    assert body["compatibility_key"] == "x86_nvidia:x86_64:12:10:8.9"
    assert body["unsupported_reasons"] == []
    assert service.node_ids == [node_id]
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        pool = session.get(ResourcePool, node.resource_pool_id)
        assert node.architecture == "x86_64"
        assert node.platform_kind == "x86_nvidia"
        assert node.capabilities["docker"]["nvidia_runtime_available"] is True
        assert node.resources["gpu_count"] == 1
        assert node.fingerprint["ssh_host_key_fingerprint"] == "SHA256:fixture"
        assert node.fingerprint["compatibility_key"] == body["compatibility_key"]
        assert pool.name == body["compatibility_key"]
        assert pool.compatibility_policy["compatibility_key"] == body["compatibility_key"]


def test_probe_marks_missing_nvidia_runtime_unsupported_with_no_pool(
    agent_api_client,
    agent_session_factory,
):
    node_id = _seed_node(agent_session_factory)
    inventory = _inventory("jetson.json")
    inventory["docker"]["runtimes"] = ["runc"]
    service = ProbeService(inventory)
    agent_api_client.app.dependency_overrides[get_edge_inventory_service] = lambda: service

    response = agent_api_client.post(f"/edge-nodes/{node_id}/probe")

    assert response.status_code == 200
    assert response.json()["supported"] is False
    assert "NVIDIA Container Runtime is unavailable" in response.json()["unsupported_reasons"]
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node.status == "incompatible"
        assert node.resource_pool_id is None
        assert node.fingerprint["unsupported_reasons"] == response.json()["unsupported_reasons"]


def test_manual_pool_move_requires_an_enabled_matching_compatibility_policy(
    agent_api_client,
    agent_session_factory,
):
    node_id = _seed_node(agent_session_factory)
    service = ProbeService(_inventory("jetson.json"))
    agent_api_client.app.dependency_overrides[get_edge_inventory_service] = lambda: service
    probe = agent_api_client.post(f"/edge-nodes/{node_id}/probe")
    assert probe.status_code == 200

    with agent_session_factory() as session:
        compatible = ResourcePool(
            name="jetson-manual",
            kind="jetson",
            selector={},
            compatibility_policy={"compatibility_key": probe.json()["compatibility_key"]},
        )
        incompatible = ResourcePool(
            name="x86-manual",
            kind="x86_nvidia",
            selector={},
            compatibility_policy={"compatibility_key": "x86_nvidia:x86_64:12:10:8.9"},
        )
        session.add_all([compatible, incompatible])
        session.commit()
        compatible_id = compatible.id
        incompatible_id = incompatible.id

    rejected = agent_api_client.put(
        f"/nodes/{node_id}/resource-pool",
        json={"resource_pool_id": incompatible_id},
    )
    accepted = agent_api_client.put(
        f"/nodes/{node_id}/resource-pool",
        json={"resource_pool_id": compatible_id},
    )

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Resource pool does not accept the node inventory"
    assert accepted.status_code == 200
    assert accepted.json()["resource_pool_id"] == compatible_id
    with agent_session_factory() as session:
        assert session.scalar(select(ComputeNode).where(ComputeNode.id == node_id)).resource_pool_id == (
            compatible_id
        )
