import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from visiox_api.routes.nodes import (
    _get_or_create_inventory_pool,
    get_edge_inventory_service,
    get_node_session,
)
from visiox_db.models import ComputeNode, ResourcePool
from visiox_edge_executor_worker.inventory import compatibility_policy, parse_inventory


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


class ConcurrentMutationProbeService(ProbeService):
    def __init__(self, inventory, session_factory, manual_pool_id: str) -> None:
        super().__init__(inventory)
        self.session_factory = session_factory
        self.manual_pool_id = manual_pool_id

    def probe(self, *, node_id: str, deadline: float | None = None) -> dict[str, object]:
        with self.session_factory() as session:
            node = session.get(ComputeNode, node_id)
            node.resource_pool_id = self.manual_pool_id
            node.status = "draining"
            node.fingerprint = {
                **node.fingerprint,
                "concurrent_owner_marker": "preserve-me",
            }
            session.commit()
        return super().probe(node_id=node_id, deadline=deadline)


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
        assert node.fingerprint["driver_cuda_compatibility_version"] == "12.4"
        assert node.fingerprint["cuda_runtime_version"] == "12.4.127-1"
        assert node.fingerprint["inventory_snapshot"]["driver_cuda_compatibility_version"] == (
            "12.4"
        )
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


def test_probe_refreshes_after_remote_wait_and_preserves_concurrent_owned_state(
    agent_api_client,
    agent_session_factory,
):
    node_id = _seed_node(agent_session_factory)
    inventory = _inventory("x86.json")
    snapshot = parse_inventory(inventory)
    with agent_session_factory() as session:
        manual_pool = ResourcePool(
            name="x86-manual-concurrent",
            kind=snapshot.platform_kind,
            selector={},
            compatibility_policy=compatibility_policy(snapshot),
            enabled=True,
        )
        session.add(manual_pool)
        session.commit()
        manual_pool_id = manual_pool.id
    service = ConcurrentMutationProbeService(
        inventory,
        agent_session_factory,
        manual_pool_id,
    )
    agent_api_client.app.dependency_overrides[get_edge_inventory_service] = lambda: service

    request_session = agent_session_factory()
    stale_node = request_session.get(ComputeNode, node_id)

    def override_request_session():
        yield request_session

    agent_api_client.app.dependency_overrides[get_node_session] = override_request_session

    try:
        response = agent_api_client.post(f"/edge-nodes/{node_id}/probe")
    finally:
        request_session.close()

    assert response.status_code == 200
    assert stale_node is not None
    assert response.json()["resource_pool_id"] == manual_pool_id
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node.resource_pool_id == manual_pool_id
        assert node.status == "draining"
        assert node.fingerprint["concurrent_owner_marker"] == "preserve-me"
        assert node.fingerprint["compatibility_key"] == response.json()["compatibility_key"]


@pytest.mark.parametrize(
    ("enabled", "kind", "policy_mutation"),
    [
        (False, "x86_nvidia", {}),
        (True, "jetson", {}),
        (True, "x86_nvidia", {"cuda_major": 11}),
    ],
)
def test_probe_rejects_non_accepting_same_name_pool(
    agent_api_client,
    agent_session_factory,
    enabled,
    kind,
    policy_mutation,
):
    node_id = _seed_node(agent_session_factory)
    inventory = _inventory("x86.json")
    snapshot = parse_inventory(inventory)
    policy = {**compatibility_policy(snapshot), **policy_mutation}
    with agent_session_factory() as session:
        session.add(
            ResourcePool(
                name="x86_nvidia:x86_64:12:10:8.9",
                kind=kind,
                selector={},
                compatibility_policy=policy,
                enabled=enabled,
            )
        )
        session.commit()
    service = ProbeService(inventory)
    agent_api_client.app.dependency_overrides[get_edge_inventory_service] = lambda: service

    response = agent_api_client.post(f"/edge-nodes/{node_id}/probe")

    assert response.status_code == 409
    assert response.json()["detail"] == "Resource pool does not accept the node inventory"
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node.resource_pool_id is None
        assert node.platform_kind == "ssh_edge"


def test_probe_rejects_disabled_pool_that_wins_creation_race(
    agent_session_factory,
    monkeypatch,
):
    snapshot = parse_inventory(_inventory("x86.json"))
    with agent_session_factory() as session:
        winning_pool = ResourcePool(
            name="x86_nvidia:x86_64:12:10:8.9",
            kind="x86_nvidia",
            selector={},
            compatibility_policy=compatibility_policy(snapshot),
            enabled=False,
        )
        session.add(winning_pool)
        session.commit()
        original_scalar = session.scalar
        hid_winning_pool = False

        def scalar_with_stale_first_pool_read(statement, *args, **kwargs):
            nonlocal hid_winning_pool
            selects_pool = any(
                description.get("entity") is ResourcePool
                for description in statement.column_descriptions
            )
            if selects_pool and not hid_winning_pool:
                hid_winning_pool = True
                return None
            return original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", scalar_with_stale_first_pool_read)

        with pytest.raises(
            HTTPException,
            match="Resource pool does not accept the node inventory",
        ) as exc_info:
            _get_or_create_inventory_pool(session, snapshot)

        assert hid_winning_pool is True
        assert exc_info.value.status_code == 409


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
            compatibility_policy=compatibility_policy(parse_inventory(_inventory("jetson.json"))),
        )
        incompatible = ResourcePool(
            name="x86-manual",
            kind="x86_nvidia",
            selector={},
            compatibility_policy=compatibility_policy(parse_inventory(_inventory("x86.json"))),
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
