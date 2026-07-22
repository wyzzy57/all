import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_db.base import Base
from visiox_db.models import ComputeNode, EdgeSshCredential
from visiox_edge_executor_worker.bootstrap_server import BootstrapOperations
from visiox_edge_executor_worker.crypto import CredentialCipher
from visiox_edge_executor_worker.inventory import compatibility_key, parse_inventory
from visiox_edge_executor_worker.scripts import load_packaged_script
from visiox_edge_executor_worker.ssh import CommandResult, RemotePrivateDirectory
from visiox_edge_executor_worker.startup import EdgeExecutorSecurityContext


FIXTURES = Path(__file__).parents[1] / "fixtures" / "edge_inventory"


@pytest.fixture()
def load_fixture():
    def load(name: str) -> dict[str, object]:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load


@pytest.mark.parametrize(
    ("fixture", "platform"),
    [("jetson.json", "jetson"), ("x86.json", "x86_nvidia")],
)
def test_inventory_snapshot_parses_supported_hosts(load_fixture, fixture, platform):
    snapshot = parse_inventory(load_fixture(fixture))

    assert snapshot.supported is True
    assert snapshot.platform_kind == platform
    assert snapshot.docker.nvidia_runtime_available is True
    assert snapshot.unsupported_reasons == ()


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("jetson.json", "jetson:aarch64:12:10:8.7"),
        ("x86.json", "x86_nvidia:x86_64:12:unknown:8.9"),
    ],
)
def test_compatibility_key_separates_jetson_and_x86(load_fixture, fixture, expected):
    assert compatibility_key(parse_inventory(load_fixture(fixture))) == expected


def test_non_tegra_aarch64_nvidia_host_is_not_classified_as_jetson(load_fixture):
    snapshot = parse_inventory(load_fixture("gh200.json"))

    assert snapshot.platform_kind == "unsupported"
    assert snapshot.supported is False
    assert "Only Ubuntu Jetson and x86 NVIDIA hosts are supported" in (
        snapshot.unsupported_reasons
    )


@pytest.mark.parametrize(
    ("fixture", "driver_ceiling", "runtime_version", "expected_key"),
    [
        ("x86.json", "13.0", "12.4.127-1", "x86_nvidia:x86_64:13:unknown:8.9"),
        ("jetson.json", "12.8", "12.2.140-1", "jetson:aarch64:12:10:8.7"),
    ],
)
def test_compatibility_uses_installed_cuda_not_driver_ceiling(
    load_fixture,
    fixture,
    driver_ceiling,
    runtime_version,
    expected_key,
):
    inventory = load_fixture(fixture)
    inventory["nvidia"]["driver_cuda_compatibility_version"] = driver_ceiling

    snapshot = parse_inventory(inventory)

    assert snapshot.driver_cuda_compatibility_version == driver_ceiling
    assert snapshot.cuda_runtime_version == runtime_version
    assert snapshot.cuda_version == runtime_version
    assert compatibility_key(snapshot) == expected_key


def test_x86_driver_and_container_runtime_do_not_require_host_cuda(load_fixture):
    inventory = load_fixture("x86.json")
    inventory["jetson"]["packages"] = {
        name: version
        for name, version in inventory["jetson"]["packages"].items()
        if not name.startswith(("cuda-cudart", "cuda-toolkit"))
    }

    snapshot = parse_inventory(inventory)

    assert snapshot.driver_cuda_compatibility_version == "12.4"
    assert snapshot.cuda_runtime_version is None
    assert snapshot.cuda_major is None
    assert snapshot.supported is True
    assert "CUDA runtime/toolkit is unavailable" not in snapshot.unsupported_reasons
    assert "TensorRT version is unavailable" not in snapshot.unsupported_reasons
    assert compatibility_key(snapshot) == "x86_nvidia:x86_64:12:unknown:8.9"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"available": False, "runtimes": []}, "Docker Engine is unavailable"),
        ({"available": True, "runtimes": ["runc"]}, "NVIDIA Container Runtime is unavailable"),
    ],
)
def test_missing_mandatory_docker_features_are_explicitly_unsupported(
    load_fixture,
    mutation,
    reason,
):
    inventory = load_fixture("x86.json")
    inventory["docker"].update(mutation)

    snapshot = parse_inventory(inventory)

    assert snapshot.supported is False
    assert reason in snapshot.unsupported_reasons


def test_probe_script_is_packaged_and_uses_only_static_probe_inputs():
    script = load_packaged_script("probe_inventory.sh").decode("utf-8")

    for fact in (
        "/etc/os-release",
        "/proc/meminfo",
        "/etc/nv_tegra_release",
        "uname",
        "docker",
        "nvidia-smi",
        "nvcc",
        "/usr/local/cuda/version.json",
        "dpkg-query",
    ):
        assert fact in script
    assert "shell=True" not in script
    assert "eval " not in script
    assert script.count("json.dumps(") == 1


class ProbeSshSession:
    def __init__(self, output: bytes) -> None:
        self.output = output
        self.uploads: list[tuple[bytes, str]] = []
        self.commands: list[str] = []
        self.cleaned_paths: tuple[str, ...] | None = None
        self.closed = False

    def create_private_directory(self, *, timeout_seconds: float) -> RemotePrivateDirectory:
        assert 0 < timeout_seconds <= 60
        return RemotePrivateDirectory("/home/visiox-edge/.visiox-random", 1000)

    def upload_bytes_exclusive(
        self,
        data: bytes,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        assert 0 < timeout_seconds <= 60
        self.uploads.append((data, remote_path))

    def validate_remote_file(
        self,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert remote_path == self.uploads[-1][1]
        assert expected_owner_uid == 1000
        assert 0 < timeout_seconds <= 60

    def run(self, command: str, *, timeout_seconds: float) -> CommandResult:
        assert 0 < timeout_seconds <= 60
        self.commands.append(command)
        return CommandResult(exit_status=0, stdout=self.output, stderr=b"")

    def cleanup_private_directory(
        self,
        workspace: RemotePrivateDirectory,
        remote_paths: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        assert workspace.path == "/home/visiox-edge/.visiox-random"
        assert 0 < timeout_seconds <= 60
        self.cleaned_paths = remote_paths

    def close(self) -> None:
        self.closed = True


class ProbeSshClient:
    def __init__(self, session: ProbeSshSession) -> None:
        self.session = session
        self.connect_request: dict[str, object] | None = None

    def connect(self, **request):
        self.connect_request = request
        return self.session


def test_bootstrap_operations_probe_uses_strict_credential_and_static_private_workspace(
    load_fixture,
):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    cipher = CredentialCipher(b"m" * 32, key_version=1)
    encrypted = cipher.encrypt(b"private-key-fixture")
    with factory() as database:
        node = ComputeNode(
            name="probe-node",
            status="online",
            architecture="unknown",
            platform_kind="ssh_edge",
            capabilities={},
            resources={},
            fingerprint={},
            agent_version="ssh-bootstrap",
        )
        database.add(node)
        database.flush()
        database.add(
            EdgeSshCredential(
                node_id=node.id,
                ssh_host="edge.internal",
                ssh_port=22,
                ssh_user="visiox-edge",
                host_key_type="ssh-ed25519",
                host_key_fingerprint="SHA256:pinned",
                public_key="ssh-ed25519 public",
                encrypted_private_key=encrypted.ciphertext,
                encryption_nonce=encrypted.nonce,
                key_version=encrypted.key_version,
            )
        )
        database.commit()
        node_id = node.id

    output = json.dumps(load_fixture("x86.json")).encode("utf-8")
    ssh_session = ProbeSshSession(output)
    ssh_client = ProbeSshClient(ssh_session)
    operations = BootstrapOperations(
        EdgeExecutorSecurityContext(cipher, ssh_client),  # type: ignore[arg-type]
        factory,
        inventory_script=b"static-probe-script",
    )

    response = operations.handle(
        {"request_id": "probe-request", "operation": "probe", "node_id": node_id}
    )

    assert response["status"] == "ok"
    assert response["inventory"]["supported"] is True
    assert ssh_client.connect_request == {
        "host": "edge.internal",
        "port": 22,
        "username": "visiox-edge",
        "private_key": b"private-key-fixture",
        "expected_fingerprint": "SHA256:pinned",
        "timeout_seconds": ssh_client.connect_request["timeout_seconds"],
    }
    assert ssh_session.uploads == [
        (
            b"static-probe-script",
            "/home/visiox-edge/.visiox-random/probe_inventory.sh",
        )
    ]
    assert ssh_session.commands == [
        "/bin/bash /home/visiox-edge/.visiox-random/probe_inventory.sh"
    ]
    assert node_id not in ssh_session.commands[0]
    assert ssh_session.cleaned_paths == (
        "/home/visiox-edge/.visiox-random/probe_inventory.sh",
    )
    assert ssh_session.closed is True
