from collections.abc import Generator
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.dependencies.auth import get_current_user
from visiox_api.routes.pipelines import get_pipeline_session
from visiox_api.routes.tasks import get_task_session
from visiox_api.routes.training_jobs import (
    get_training_job_session,
    get_training_object_storage_client,
    get_training_stream_producer,
)
from visiox_common.tasks import TaskStatus, TaskType
from visiox_common.settings import Settings, get_settings
from visiox_db.models import (
    Annotation,
    BaseModel,
    ComputeNode,
    Dataset,
    DatasetSample,
    DatasetVersion,
    DistributedTrainingRun,
    EdgeSshCredential,
    LogStream,
    RemoteExecution,
    ResourcePool,
    Task,
    TrainedModel,
    TrainingJob,
    TrainingJobAttempt,
    TrainingPipeline,
    Organization,
    User,
)
from visiox_edge_executor_worker.inventory import (
    compatibility_key,
    compatibility_policy,
    parse_inventory,
)
from visiox_storage.client import InMemoryObjectStorageClient
from visiox_training.contracts import LaunchSpec
from tests.integration.ownership_test_support import install_legacy_ownership
import visiox_training_worker.main as training_worker_main
from visiox_training_worker.main import CommandResult, run_training_job
from visiox_training_worker.runner import (
    SubprocessTrainingRunner,
    run_pending_training_tasks,
)


LEGACY_TEST_ACTOR = SimpleNamespace(
    id="legacy-admin", organization_id="legacy-org", role="admin"
)


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []
        self.edge_commands: list[dict[str, object]] = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"

    async def enqueue_edge_execution(self, **command):
        self.edge_commands.append(command)
        return "2-0"


class FailingStreamProducer:
    async def enqueue(self, command):
        raise RuntimeError("redis unavailable")

    async def enqueue_edge_execution(self, **command):
        del command
        raise RuntimeError("redis unavailable")


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, argv: list[str], work_dir: Path, should_cancel=None) -> CommandResult:
        del should_cancel
        self.commands.append(argv)
        weights_dir = work_dir / "runs" / "train" / "weights"
        weights_dir.mkdir(parents=True)
        artifact = weights_dir / "best.pt"
        last = weights_dir / "last.pt"
        artifact.write_bytes(b"best model")
        last.write_bytes(b"last model")
        return CommandResult(
            exit_code=0,
            stdout="mAP50=0.91",
            stderr="",
            metrics={"mAP50": 0.91, "precision": 0.88},
            artifact_path=artifact,
            weight_paths={"best.pt": artifact, "last.pt": last},
        )


class FailingRunner:
    def run(self, argv: list[str], work_dir: Path, should_cancel=None) -> CommandResult:
        del argv, work_dir, should_cancel
        raise RuntimeError("cuda out of memory")


class CancelingRunner:
    def __init__(self, session_factory, task_id: str) -> None:
        self.session_factory = session_factory
        self.task_id = task_id

    def run(self, argv: list[str], work_dir: Path, should_cancel=None) -> CommandResult:
        del argv, work_dir
        with self.session_factory() as session:
            task = session.get(Task, self.task_id)
            task.status = TaskStatus.CANCELED.value
            session.add(task)
            session.commit()
        if should_cancel is not None:
            assert should_cancel() is True
        return CommandResult(exit_code=130, stderr="training canceled")


class InspectingRunner:
    def __init__(self) -> None:
        self.base_model_bytes: bytes | None = None

    def run(self, argv: list[str], work_dir: Path, should_cancel=None) -> CommandResult:
        del should_cancel
        model_arg = next(arg for arg in argv if arg.startswith("model="))
        self.base_model_bytes = Path(model_arg.split("=", 1)[1]).read_bytes()
        artifact = work_dir / "runs" / "train" / "weights" / "best.pt"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"trained model")
        return CommandResult(exit_code=0, artifact_path=artifact)


class FailingLogStorage(InMemoryObjectStorageClient):
    def put_file(
        self,
        bucket: str,
        object_name: str,
        file_path: Path,
        content_type: str | None = None,
    ) -> str:
        if bucket == "training":
            raise RuntimeError("log upload failed")
        return super().put_file(bucket, object_name, file_path, content_type)


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-training.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with Session(engine) as session:
        session.add(Organization(id="legacy-org", name="Legacy", slug="legacy"))
        session.add(
            User(
                id="legacy-admin",
                organization_id="legacy-org",
                username="legacy-admin",
                display_name="Legacy Admin",
                email="legacy-admin@example.test",
                password_hash="test",
                role="admin",
                status="active",
                must_change_password=False,
            )
        )
        session.commit()

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(factory)
    return factory


@pytest.fixture()
def stream_producer() -> FakeStreamProducer:
    return FakeStreamProducer()


@pytest.fixture()
def client(
    session_factory, stream_producer: FakeStreamProducer
) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_session] = override_session
    app.dependency_overrides[get_task_session] = override_session
    app.dependency_overrides[get_training_job_session] = override_session
    app.dependency_overrides[get_training_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        ultralytics_training_image_digest=f"registry.example/visiox/ultralytics-training@sha256:{'a' * 64}",
        paddlex_training_image_digest=f"registry.example/visiox/paddlex-training@sha256:{'c' * 64}",
        llm_training_image_digest=f"registry.example/visiox/llm-training@sha256:{'b' * 64}",
    )

    with TestClient(app) as test_client:
        yield test_client


def seed_training_ready_rows(
    session_factory,
    storage: InMemoryObjectStorageClient | None = None,
    tmp_path: Path | None = None,
    *,
    task: str = "detect",
    scale: str = "n",
    base_status: str = "ready",
    base_filename: str | None = None,
    dataset_task: str | None = None,
    dataset_status: str = "validated",
    sample_count: int = 1,
    annotation_count: int = 1,
) -> tuple[str, str, str | None]:
    dataset_task = dataset_task or task
    with session_factory() as session:
        base_model = BaseModel(
            family="yolo26",
            task=task,
            scale=scale,
            filename=base_filename or f"yolo26{scale}-{task}.pt",
            source_path=f"yolo26{scale}-{task}.pt",
            local_uri=f"memory://models/base/{task}-{scale}.pt",
            checksum="a" * 64,
            status=base_status,
        )
        dataset = Dataset(
            name=f"{dataset_task}-dataset-{base_status}-{sample_count}-{annotation_count}",
            task=dataset_task,
            status=dataset_status,
            class_schema={"names": ["ok", "defect"]},
            sample_count=sample_count,
            annotation_count=annotation_count,
            source="upload",
            manifest_checksum="b" * 64,
            storage_uri=f"memory://datasets/{dataset_task}/current",
        )
        session.add_all([base_model, dataset])
        session.flush()
        session.add(
            DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="published",
                format=dataset.format,
                object_uri=f"memory://datasets/{dataset.id}/versions/1",
                manifest_uri=f"memory://datasets/{dataset.id}/versions/1/manifest.json",
                manifest_checksum="b" * 64,
                total_count=sample_count,
                valid_count=sample_count,
                invalid_count=0,
                skipped_count=0,
                size_bytes=0,
                schema_snapshot={},
                published_at=datetime.now(UTC),
            )
        )
        sample_id = None
        if sample_count:
            sample = DatasetSample(
                dataset_id=dataset.id,
                file_uri="memory://datasets/train/sample.png",
                width=100,
                height=80,
                checksum=f"sample-{dataset.id}",
                split="train",
                annotation_status="labeled" if annotation_count else "pending",
            )
            session.add(sample)
            session.flush()
            sample_id = sample.id
            if annotation_count:
                session.add(
                    Annotation(
                        dataset_sample_id=sample.id,
                        source="label_studio",
                        internal_payload={
                            "annotations": [
                                {
                                    "source_annotation_id": "annotation-1",
                                    "results": [
                                        {
                                            "source_result_id": "rect-1",
                                            "shape": "rectangle",
                                            "class_name": "defect",
                                            "x": 10,
                                            "y": 20,
                                            "width": 30,
                                            "height": 40,
                                        }
                                    ],
                                }
                            ]
                        },
                        validation_status="valid",
                    )
                )
        session.commit()
        base_model_id = base_model.id
        dataset_id = dataset.id

    if storage is not None and tmp_path is not None:
        base_path = tmp_path / "base.pt"
        base_path.write_bytes(b"base model")
        storage.put_file("models", f"base/{task}-{scale}.pt", base_path)
        image_path = tmp_path / "sample.png"
        Image.new("RGB", (100, 80), color=(12, 34, 56)).save(image_path)
        storage.put_file("datasets", "train/sample.png", image_path)

    return base_model_id, dataset_id, sample_id


def seed_distributed_pool(
    session_factory, *, name: str = "training-pool", node_count: int = 2
) -> tuple[str, list[str]]:
    snapshot = parse_inventory(
        json.loads(
            Path("tests/fixtures/edge_inventory/x86.json").read_text(encoding="utf-8")
        )
    )
    key = compatibility_key(snapshot)
    with session_factory() as session:
        pool = ResourcePool(
            name=name,
            kind=snapshot.platform_kind,
            selector=compatibility_policy(snapshot),
            compatibility_policy=compatibility_policy(snapshot),
            enabled=True,
        )
        session.add(pool)
        session.flush()
        nodes: list[ComputeNode] = []
        for index in range(node_count):
            node_snapshot = snapshot.model_copy(
                update={
                    "gpus": tuple(
                        gpu.model_copy(
                            update={"uuid": f"GPU-{name}-{index}-{gpu_index}"}
                        )
                        for gpu_index, gpu in enumerate(snapshot.gpus)
                    )
                }
            )
            node = ComputeNode(
                name=f"{name}-node-{index}",
                resource_pool_id=pool.id,
                status="online",
                architecture=snapshot.architecture,
                platform_kind=snapshot.platform_kind,
                capabilities={"nvidia_gpu": True},
                resources={"gpu_count": len(node_snapshot.gpus)},
                fingerprint={
                    "compatibility_key": key,
                    "inventory_snapshot": node_snapshot.model_dump(mode="json"),
                },
                agent_version="ssh-edge-v1",
            )
            session.add(node)
            session.flush()
            session.add(
                EdgeSshCredential(
                    node_id=node.id,
                    ssh_host=f"{name}-node-{index}.lan",
                    ssh_port=22,
                    ssh_user="visiox-edge",
                    host_key_type="ssh-ed25519",
                    host_key_fingerprint=f"SHA256:{name}-{index}",
                    public_key=f"ssh-ed25519 {name}-{index}",
                    encrypted_private_key=b"encrypted",
                    encryption_nonce=b"nonce",
                )
            )
            nodes.append(node)
        session.commit()
        return pool.id, [node.id for node in nodes]


def create_pipeline(
    client: TestClient, base_model_id: str, dataset_id: str, **overrides
):
    payload = {
        "name": "detect-training",
        "task": "detect",
        "scale": "n",
        "base_model_id": base_model_id,
        "dataset_id": dataset_id,
        "params_template": {"epochs": 10, "batch": 4, "imgsz": 640},
        "default_environment": {"device": "cpu", "workers": 2},
    }
    payload.update(overrides)
    return client.post("/pipelines", json=payload)


def test_create_pipeline_validates_base_model_dataset_and_lists_filters(
    client: TestClient, session_factory
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)

    response = create_pipeline(client, base_model_id, dataset_id)
    by_task = client.get("/pipelines?task=detect")
    by_status = client.get("/pipelines?status=ready")
    detail = client.get(f"/pipelines/{response.json()['id']}")

    assert response.status_code == 201
    body = response.json()
    assert body["task"] == "detect"
    assert body["scale"] == "n"
    assert body["base_model_id"] == base_model_id
    assert body["dataset_id"] == dataset_id
    assert body["params_template"] == {"epochs": 10, "batch": 4, "imgsz": 640}
    assert body["default_environment"] == {"device": "cpu", "workers": 2}
    assert body["status"] == "ready"
    assert by_task.json()["total"] == 1
    assert by_status.json()["items"][0]["id"] == body["id"]
    assert detail.json()["id"] == body["id"]


def test_create_pipeline_without_training_resources_persists_draft(client: TestClient):
    response = client.post(
        "/pipelines",
        json={"name": "draft-pipeline", "task": "detect", "scale": "n"},
    )
    partial = client.post(
        "/pipelines",
        json={
            "name": "invalid-draft",
            "task": "detect",
            "scale": "n",
            "base_model_id": "base-only",
        },
    )
    duplicate = client.post(
        "/pipelines",
        json={"name": "draft-pipeline", "task": "detect", "scale": "n"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["base_model_id"] is None
    assert body["dataset_id"] is None
    listed = client.get("/pipelines?status=draft")
    assert any(item["id"] == body["id"] for item in listed.json()["items"])
    assert partial.status_code == 422
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "产线名称已存在，请使用其他名称"


@pytest.mark.parametrize(
    ("name", "explicit", "legacy", "expected_family", "expected_model"),
    [
        (
            "explicit-ultralytics",
            {
                "task_kind": "object_detection",
                "framework": "ultralytics",
                "adapter_key": "ultralytics.object_detection.v1",
                "adapter_version": "1.0.0",
                "model_family": "yolo26",
                "recipe": {"model": "YOLO26-N"},
            },
            {"engine": "yolo26", "task": "detect", "scale": "n"},
            "YOLO26",
            ("YOLO26-N", "yolo26n.pt", "N"),
        ),
        (
            "explicit-paddlex-ppyoloe",
            {
                "task_kind": "object_detection",
                "framework": "paddlex",
                "adapter_key": "paddlex.object_detection.v1",
                "adapter_version": "1.0.0",
                "model_family": "PP-YOLOE",
                "recipe": {"model": "PP-YOLOE-S"},
            },
            {"engine": "paddlex", "task": "detect", "scale": "s"},
            "PP-YOLOE",
            ("PP-YOLOE-S", "PP-YOLOE_plus-S", "S"),
        ),
        (
            "explicit-paddlex-rtdetr",
            {
                "task_kind": "object_detection",
                "framework": "paddlex",
                "adapter_key": "paddlex.object_detection.v1",
                "adapter_version": "1.0.0",
                "model_family": "RT-DETR",
                "recipe": {"model": "RT-DETR-L"},
            },
            {"engine": "paddlex", "task": "detect", "scale": "l"},
            "RT-DETR",
            ("RT-DETR-L", "RT-DETR-L", "L"),
        ),
        (
            "explicit-llamafactory",
            {
                "task_kind": "llm_sft",
                "framework": "llamafactory",
                "adapter_key": "llamafactory.llm_sft.v1",
                "adapter_version": "1.0.0",
                "model_family": "Qwen3",
                "recipe": {"model": "Qwen/Qwen3-0.6B"},
            },
            {"engine": "llamafactory", "task": "llm", "scale": "0.6B"},
            "Qwen3",
            ("Qwen3 0.6B", "Qwen/Qwen3-0.6B", "0.6B"),
        ),
    ],
)
def test_create_explicit_adapter_drafts_resolves_catalog_models(
    client: TestClient,
    name: str,
    explicit: dict,
    legacy: dict,
    expected_family: str,
    expected_model: tuple[str, str, str],
) -> None:
    response = client.post("/pipelines", json={"name": name, **legacy, **explicit})

    assert response.status_code == 201, response.text
    body = response.json()
    assert {key: body[key] for key in explicit if key != "recipe"} == {
        **{
            key: explicit[key]
            for key in explicit
            if key not in {"recipe", "model_family"}
        },
        "model_family": expected_family,
    }
    assert {key: body[key] for key in legacy} == legacy
    assert body["recipe"]["model"] == {
        "key": body["recipe"]["model"]["key"],
        "label": expected_model[0],
        "runtime_id": expected_model[1],
        "family": expected_family,
        "variant": expected_model[2],
    }
    assert body["status"] == "draft"


def test_create_paddlex_pipeline_is_ready_with_published_coco_version(
    client: TestClient,
    session_factory,
) -> None:
    _, dataset_id, _ = seed_training_ready_rows(session_factory)
    with session_factory() as session:
        version = session.scalar(
            select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
        )
        assert version is not None
        version.format = "coco"
        session.commit()

    response = client.post(
        "/pipelines",
        json={
            "name": "ready-paddlex-pipeline",
            "task_kind": "object_detection",
            "framework": "paddlex",
            "adapter_key": "paddlex.object_detection.v1",
            "adapter_version": "1.0.0",
            "recipe": {"model": "PP-YOLOE-S"},
            "dataset_id": dataset_id,
            "params_template": {"epochs": 2, "batch_size": 1},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "ready"


def test_create_legacy_pipeline_returns_explicit_identity(client: TestClient) -> None:
    yolo = client.post(
        "/pipelines",
        json={
            "name": "legacy-yolo-identity",
            "engine": "yolo26",
            "task": "detect",
        },
    )
    llama = client.post(
        "/pipelines",
        json={
            "name": "legacy-llama-identity",
            "engine": "llamafactory",
            "task": "llm",
            "scale": "llm",
        },
    )

    assert yolo.status_code == 201, yolo.text
    assert {
        key: yolo.json()[key]
        for key in (
            "task_kind",
            "framework",
            "adapter_key",
            "adapter_version",
            "model_family",
        )
    } == {
        "task_kind": "object_detection",
        "framework": "ultralytics",
        "adapter_key": "ultralytics.object_detection.v1",
        "adapter_version": "1.0.0",
        "model_family": "yolo26",
    }
    assert yolo.json()["scale"] == "n"
    assert yolo.json()["recipe"]["model"] == {
        "key": "yolo26n",
        "label": "YOLO26-N",
        "runtime_id": "yolo26n.pt",
        "family": "YOLO26",
        "variant": "N",
    }
    assert llama.status_code == 201, llama.text
    assert llama.json()["task_kind"] == "llm_sft"
    assert llama.json()["framework"] == "llamafactory"
    assert llama.json()["adapter_key"] == "llamafactory.llm_sft.v1"


def test_update_unlocked_pipeline_switches_framework_after_revalidation(
    client: TestClient,
) -> None:
    created = client.post(
        "/pipelines",
        json={
            "name": "switchable-draft",
            "engine": "yolo26",
            "task": "detect",
            "scale": "n",
        },
    )

    updated = client.patch(
        f"/pipelines/{created.json()['id']}",
        json={
            "engine": "paddlex",
            "task": "detect",
            "scale": "s",
            "task_kind": "object_detection",
            "framework": "paddlex",
            "adapter_key": "paddlex.object_detection.v1",
            "adapter_version": "1.0.0",
            "model_family": "PP-YOLOE",
            "recipe": {"model": "PP-YOLOE-S"},
        },
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["framework"] == "paddlex"
    assert updated.json()["recipe"]["model"]["runtime_id"] == "PP-YOLOE_plus-S"
    assert updated.json()["status"] == "draft"


@pytest.mark.parametrize(
    "identity_change",
    [
        {"task_kind": "llm_sft"},
        {"framework": "paddlex"},
        {"adapter_key": "paddlex.object_detection.v1"},
        {"adapter_version": "2.0.0"},
        {"model_family": "PP-YOLOE"},
        {"engine": "paddlex"},
        {"task": "segment"},
        {"scale": "s"},
    ],
)
def test_update_locked_pipeline_rejects_explicit_and_legacy_identity_changes(
    client: TestClient,
    session_factory,
    identity_change: dict,
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    created = create_pipeline(
        client,
        base_model_id,
        dataset_id,
        name=f"locked-{sorted(identity_change)[0]}",
    )
    submitted = client.post(f"/pipelines/{created.json()['id']}/jobs", json={})

    assert submitted.status_code == 201, submitted.text
    with session_factory() as session:
        locked = session.get(TrainingPipeline, created.json()["id"])
        assert locked is not None
        assert locked.framework_locked_at is not None
        assert locked.first_submitted_job_id == submitted.json()["id"]

    rejected = client.patch(
        f"/pipelines/{created.json()['id']}",
        json=identity_change,
    )

    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == (
        "Pipeline framework identity is locked; clone the pipeline to change it"
    )


@pytest.mark.parametrize(
    "equal_identity",
    [
        {"task_kind": "object_detection"},
        {"framework": "ultralytics"},
        {"adapter_key": "ultralytics.object_detection.v1"},
        {"adapter_version": "1.0"},
        {"model_family": "YOLO26"},
        {"engine": "yolo26"},
        {"task": "detect"},
        {"scale": "N"},
    ],
)
def test_update_locked_pipeline_allows_equivalent_partial_identity_fields(
    client: TestClient,
    session_factory,
    equal_identity: dict,
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    created = create_pipeline(
        client,
        base_model_id,
        dataset_id,
        name=f"locked-equal-{sorted(equal_identity)[0]}",
    )
    submitted = client.post(f"/pipelines/{created.json()['id']}/jobs", json={})

    assert submitted.status_code == 201, submitted.text
    updated = client.patch(
        f"/pipelines/{created.json()['id']}",
        json=equal_identity,
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "running"


def test_pipeline_identity_payload_rejects_conflicts_and_unknown_fields(
    client: TestClient,
) -> None:
    conflicting = client.post(
        "/pipelines",
        json={
            "name": "conflicting-identity",
            "engine": "yolo26",
            "task": "detect",
            "scale": "n",
            "task_kind": "object_detection",
            "framework": "paddlex",
            "adapter_key": "paddlex.object_detection.v1",
            "adapter_version": "1.0.0",
            "model_family": "PP-YOLOE",
            "recipe": {"model": "PP-YOLOE-S"},
        },
    )
    unknown = client.post(
        "/pipelines",
        json={
            "name": "unknown-identity-field",
            "task": "detect",
            "scale": "n",
            "adapter_claim": "untrusted",
        },
    )

    assert conflicting.status_code == 422
    assert conflicting.json()["detail"] == "framework conflicts with legacy engine"
    assert unknown.status_code == 422


def test_explicit_pipeline_rejects_adapter_model_and_dataset_mismatches(
    client: TestClient,
    session_factory,
) -> None:
    with session_factory() as session:
        dataset = Dataset(
            name="paddlex-wrong-task",
            task="llm",
            status="validated",
            sample_count=1,
            annotation_count=1,
        )
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id

    base = {
        "task_kind": "object_detection",
        "framework": "paddlex",
        "adapter_key": "paddlex.object_detection.v1",
        "adapter_version": "1.0.0",
        "model_family": "PP-YOLOE",
        "recipe": {"model": "PP-YOLOE-S"},
    }
    wrong_adapter = client.post(
        "/pipelines",
        json={
            "name": "wrong-adapter",
            **base,
            "adapter_key": "ultralytics.object_detection.v1",
        },
    )
    forged_model = client.post(
        "/pipelines",
        json={
            "name": "forged-model-runtime",
            **base,
            "recipe": {
                "model": {
                    "label": "PP-YOLOE-S",
                    "runtime_id": "arbitrary-runtime-id",
                }
            },
        },
    )
    wrong_dataset = client.post(
        "/pipelines",
        json={"name": "wrong-paddlex-dataset", **base, "dataset_id": dataset_id},
    )

    assert wrong_adapter.status_code == 422
    assert wrong_adapter.json()["detail"] == (
        "Framework 'paddlex' does not support task 'object_detection'"
    )
    assert forged_model.status_code == 422
    assert forged_model.json()["detail"] == (
        "recipe model runtime_id does not match the adapter catalog"
    )
    assert wrong_dataset.status_code == 422
    assert wrong_dataset.json()["detail"] == "PaddleX dataset task must be detect"


def test_clone_locked_pipeline_copies_configuration_without_jobs(
    client: TestClient,
    session_factory,
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    source = create_pipeline(
        client,
        base_model_id,
        dataset_id,
        name="clone-source",
    )
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, source.json()["id"])
        assert pipeline is not None
        pipeline.is_public = True
        pipeline.public_scope = {"groups": ["quality"]}
        job = TrainingJob(
            pipeline_id=pipeline.id,
            status="succeeded",
            params={},
            metrics={},
            resolved_snapshot={},
            organization_id=pipeline.organization_id,
            owner_user_id=pipeline.owner_user_id,
            visibility="private",
        )
        session.add(job)
        session.flush()
        pipeline.framework_locked_at = datetime.now(UTC)
        pipeline.first_submitted_job_id = job.id
        session.add(pipeline)
        session.commit()

    cloned = client.post(
        f"/pipelines/{source.json()['id']}/clone",
        json={"name": "clone-copy"},
    )

    assert cloned.status_code == 201, cloned.text
    body = cloned.json()
    assert body["cloned_from_pipeline_id"] == source.json()["id"]
    assert body["framework_locked_at"] is None
    assert body["first_submitted_job_id"] is None
    assert body["status"] == "ready"
    assert body["dataset_id"] == dataset_id
    assert body["base_model_id"] == base_model_id
    assert body["params_template"] == source.json()["params_template"]
    assert body["default_environment"] == source.json()["default_environment"]
    assert body["is_public"] is True
    assert body["public_scope"] == {"groups": ["quality"]}
    with session_factory() as session:
        assert (
            session.scalars(
                select(TrainingJob).where(TrainingJob.pipeline_id == body["id"])
            ).all()
            == []
        )
        assert (
            session.scalars(
                select(TrainedModel).where(TrainedModel.pipeline_id == body["id"])
            ).all()
            == []
        )


def test_delete_clone_source_preserves_clone_and_clears_source_reference(
    client: TestClient,
    session_factory,
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    source = create_pipeline(
        client,
        base_model_id,
        dataset_id,
        name="clone-delete-source",
    )
    cloned = client.post(
        f"/pipelines/{source.json()['id']}/clone",
        json={"name": "clone-delete-child"},
    )
    assert cloned.status_code == 201, cloned.text

    deleted = client.delete(f"/pipelines/{source.json()['id']}")

    assert deleted.status_code == 204, deleted.text
    with session_factory() as session:
        assert session.get(TrainingPipeline, source.json()["id"]) is None
        child = session.get(TrainingPipeline, cloned.json()["id"])
        assert child is not None
        assert child.cloned_from_pipeline_id is None


def test_clone_pipeline_revalidates_framework_override_and_name_collision(
    client: TestClient,
) -> None:
    source = client.post(
        "/pipelines",
        json={
            "name": "clone-override-source",
            "engine": "yolo26",
            "task": "detect",
            "scale": "n",
        },
    )
    overridden = client.post(
        f"/pipelines/{source.json()['id']}/clone",
        json={
            "name": "clone-override-target",
            "engine": "paddlex",
            "task": "detect",
            "scale": "l",
            "task_kind": "object_detection",
            "framework": "paddlex",
            "adapter_key": "paddlex.object_detection.v1",
            "adapter_version": "1.0.0",
            "model_family": "RT-DETR",
            "recipe": {"model": "RT-DETR-L"},
            "base_model_id": None,
        },
    )
    collision = client.post(
        f"/pipelines/{source.json()['id']}/clone",
        json={"name": "clone-override-target"},
    )

    assert overridden.status_code == 201, overridden.text
    assert overridden.json()["framework"] == "paddlex"
    assert overridden.json()["model_family"] == "RT-DETR"
    assert overridden.json()["recipe"]["model"]["runtime_id"] == "RT-DETR-L"
    assert collision.status_code == 409
    assert collision.json()["detail"] == "Pipeline name already exists"


def test_create_and_update_llamafactory_pipeline_without_yolo_base_model(
    client: TestClient, session_factory
):
    created = client.post(
        "/pipelines",
        json={
            "name": "llm-sft-draft",
            "engine": "llamafactory",
            "task": "llm",
            "scale": "llm",
        },
    )

    assert created.status_code == 201
    assert created.json()["engine"] == "llamafactory"
    assert created.json()["status"] == "draft"
    assert created.json()["base_model_id"] is None

    with session_factory() as session:
        dataset = Dataset(
            name="alpaca-sft",
            task="llm",
            status="validated",
            class_schema={"format": "alpaca"},
            sample_count=12,
            annotation_count=12,
            source="upload",
            storage_uri="memory://datasets/alpaca.json",
        )
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id

    updated = client.patch(
        f"/pipelines/{created.json()['id']}",
        json={
            "engine": "llamafactory",
            "task": "llm",
            "scale": "llm",
            "dataset_id": dataset_id,
            "params_template": {
                "model_source": "huggingface",
                "model_id": "Qwen/Qwen3-0.6B",
                "model_revision": "main",
                "stage": "sft",
                "finetuning_type": "lora",
                "quantization_bit": 4,
                "learning_rate": 0.0001,
                "num_train_epochs": 3,
                "cutoff_len": 1024,
                "per_device_train_batch_size": 1,
                "gradient_accumulation_steps": 8,
            },
            "default_environment": {"device": "remote", "workers": 0},
        },
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["status"] == "ready"
    assert body["dataset_id"] == dataset_id
    assert body["params_template"]["model_id"] == "Qwen/Qwen3-0.6B"


def test_llamafactory_pipeline_accepts_explicit_external_model_identity(client: TestClient):
    model_id = "acme/domain-model-7b"
    created = client.post(
        "/pipelines",
        json={
            "name": "llm-custom-model-identity",
            "task_kind": "llm_sft",
            "framework": "llamafactory",
            "adapter_key": "llamafactory.llm_sft.v1",
            "model_family": model_id,
            "recipe": {
                "model": {
                    "key": model_id,
                    "runtime_id": model_id,
                    "family": model_id,
                    "variant": "custom",
                }
            },
            "params_template": {"model_id": model_id},
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["model_family"] == model_id
    assert body["recipe"]["model"]["runtime_id"] == model_id
    assert body["params_template"]["model_id"] == model_id


def test_llamafactory_pipeline_rejects_managed_fields_and_non_llm_dataset(
    client: TestClient, session_factory
):
    with session_factory() as session:
        dataset = Dataset(
            name="image-dataset",
            task="detect",
            status="validated",
            sample_count=2,
            annotation_count=2,
        )
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id

    managed = client.post(
        "/pipelines",
        json={
            "name": "managed-field",
            "engine": "llamafactory",
            "task": "llm",
            "params_template": {"model_name_or_path": "C:/models/qwen"},
        },
    )
    wrong_dataset = client.post(
        "/pipelines",
        json={
            "name": "wrong-dataset",
            "engine": "llamafactory",
            "task": "llm",
            "dataset_id": dataset_id,
            "params_template": {"model_id": "Qwen/Qwen3-0.6B"},
        },
    )

    assert managed.status_code == 422
    assert "managed LLaMA-Factory fields" in managed.json()["detail"]
    assert wrong_dataset.status_code == 422
    assert wrong_dataset.json()["detail"] == "dataset task must be llm"


def test_create_pipeline_with_uploaded_base_model_persists_draft(
    client: TestClient, session_factory
):
    with session_factory() as session:
        model = BaseModel(
            family="custom-model",
            task="detect",
            scale="n",
            filename="custom.pt",
            source_path="upload://custom.pt",
            local_uri="memory://models/custom/custom.pt",
            checksum="a" * 64,
            size_bytes=2048,
            status="ready",
        )
        session.add(model)
        session.commit()
        model_id = model.id

    response = client.post(
        "/pipelines",
        json={
            "name": "local-model-draft",
            "task": "detect",
            "scale": "n",
            "base_model_id": model_id,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "draft"
    assert response.json()["base_model_id"] == model_id


def test_update_and_delete_pipeline_visibility_settings(
    client: TestClient, session_factory
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    created = create_pipeline(client, base_model_id, dataset_id)
    pipeline_id = created.json()["id"]

    updated = client.patch(
        f"/pipelines/{pipeline_id}",
        json={
            "name": "renamed-detect",
            "is_public": True,
            "is_favorite": True,
            "public_scope": {"departments": ["管理员部门"], "groups": ["1组"]},
        },
    )
    deleted = client.delete(f"/pipelines/{pipeline_id}")
    missing = client.get(f"/pipelines/{pipeline_id}")

    assert updated.status_code == 200
    assert updated.json()["name"] == "renamed-detect"
    assert updated.json()["is_public"] is True
    assert updated.json()["is_favorite"] is True
    assert updated.json()["public_scope"] == {
        "departments": ["管理员部门"],
        "groups": ["1组"],
    }
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_update_pipeline_revalidates_and_syncs_base_model_task_scale(
    client: TestClient, session_factory
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(
        session_factory, scale="l"
    )
    created = create_pipeline(client, base_model_id, dataset_id, scale="l")
    pipeline_id = created.json()["id"]
    with session_factory() as session:
        base_model = BaseModel(
            family="yolo26",
            task="detect",
            scale="n",
            filename="yolo26n.pt",
            source_path="yolo26n.pt",
            local_uri="memory://models/base/detect-n.pt",
            status="ready",
        )
        session.add(base_model)
        session.commit()
        next_base_model_id = base_model.id

    updated = client.patch(
        f"/pipelines/{pipeline_id}", json={"base_model_id": next_base_model_id}
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["base_model_id"] == next_base_model_id
    assert body["task"] == "detect"
    assert body["scale"] == "n"


def test_create_pipeline_accepts_ultralytics_yolo26_training_params(
    client: TestClient, session_factory
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)

    response = create_pipeline(
        client,
        base_model_id,
        dataset_id,
        params_template={
            "epochs": 40,
            "batch": -1,
            "imgsz": 640,
            "lr0": 0.005,
            "lrf": 0.05,
            "optimizer": "MuSGD",
            "warmup_epochs": 3.0,
            "save_period": 1,
            "amp": True,
            "cos_lr": True,
            "close_mosaic": 10,
            "box": 5.6,
            "cls": 0.56,
            "dfl": 9.0,
            "hsv_h": 0.015,
            "mosaic": 0.9,
            "classes": [0, 1],
            "save_json": True,
            "augment": True,
            "agnostic_nms": True,
            "vid_stride": 2,
            "line_width": 3,
            "format": "onnx",
            "embed": [10, 12],
            "tracker": "bytetrack.yaml",
        },
    )

    assert response.status_code == 201
    params = response.json()["params_template"]
    assert params["optimizer"] == "MuSGD"
    assert params["batch"] == -1
    assert params["classes"] == [0, 1]
    assert params["cos_lr"] is True
    assert params["warmup_epochs"] == 3.0
    assert params["save_json"] is True
    assert params["format"] == "onnx"
    assert params["embed"] == [10, 12]


def test_create_pipeline_maps_legacy_warmup_steps_to_ultralytics_warmup_epochs(
    client: TestClient, session_factory
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)

    response = create_pipeline(
        client,
        base_model_id,
        dataset_id,
        params_template={"epochs": 40, "batch": 4, "warmup_steps": 5},
    )

    assert response.status_code == 201
    params = response.json()["params_template"]
    assert "warmup_steps" not in params
    assert params["warmup_epochs"] == 5.0


@pytest.mark.parametrize(
    ("seed_kwargs", "payload_overrides", "expected_status"),
    [
        ({"base_status": "pending"}, {}, 409),
        ({"dataset_status": "created"}, {}, 409),
        ({}, {"scale": "s"}, 409),
        ({"dataset_task": "segment"}, {}, 409),
        ({"sample_count": 0, "annotation_count": 0}, {}, 409),
        ({"annotation_count": 0}, {}, 409),
        ({}, {"params_template": {"epochs": -1}}, 422),
        ({}, {"params_template": {"epochs": 1, "unknown": 2}}, 422),
        ({}, {"params_template": {"epochs": 1, "model": "other.pt"}}, 422),
        ({}, {"default_environment": {"image": "bad"}}, 422),
    ],
)
def test_create_pipeline_rejects_invalid_prechecks_and_params(
    client: TestClient,
    session_factory,
    seed_kwargs,
    payload_overrides,
    expected_status: int,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(
        session_factory, **seed_kwargs
    )

    response = create_pipeline(client, base_model_id, dataset_id, **payload_overrides)

    assert response.status_code == expected_status


def test_create_training_job_creates_task_and_enqueues_command(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]

    response = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "params": {"epochs": 3, "seed": 42},
            "environment": {"device": "0", "workers": 1},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["pipeline_id"] == pipeline_id
    assert body["status"] == "queued"
    assert body["params"] == {"epochs": 3, "batch": 4, "imgsz": 640, "seed": 42}
    assert body["environment"] == {"device": "0", "workers": 1}
    assert body["task_id"]
    assert len(stream_producer.commands) == 1
    command = stream_producer.commands[0]
    assert command.task_type == TaskType.TRAIN_MODEL
    assert command.resource_refs == {"training_job_id": body["id"]}
    assert command.payload == {
        "pipeline_id": pipeline_id,
        "training_job_id": body["id"],
        "dataset_id": dataset_id,
        "base_model_id": base_model_id,
        "params": body["params"],
        "environment": body["environment"],
    }

    list_response = client.get(
        f"/training-jobs?pipeline_id={pipeline_id}&status=queued"
    )
    detail_response = client.get(f"/training-jobs/{body['id']}")
    assert list_response.json()["total"] == 1
    assert detail_response.json()["task_id"] == body["task_id"]
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, pipeline_id)
        job = session.get(TrainingJob, body["id"])
        attempt = session.scalar(
            select(TrainingJobAttempt).where(
                TrainingJobAttempt.training_job_id == body["id"]
            )
        )
        assert pipeline.status == "running"
        assert pipeline.framework_locked_at is not None
        assert pipeline.first_submitted_job_id == body["id"]
        assert attempt is not None
        assert attempt.attempt_number == 1
        assert attempt.launch_spec["adapter_key"] == "ultralytics.object_detection.v1"
        assert job.launch_spec_checksum
        assert attempt.launch_spec_checksum == job.launch_spec_checksum
        assert (
            attempt.launch_spec_checksum
            == LaunchSpec.model_validate(
                attempt.launch_spec
            ).canonical_checksum_sha256()
        )
        assert job.resolved_snapshot == {
            "schema_version": "1.0",
            "task_kind": "object_detection",
            "framework": "ultralytics",
            "adapter_key": "ultralytics.object_detection.v1",
            "adapter_version": "1.0.0",
            "runtime_image_digest": f"registry.example/visiox/ultralytics-training@sha256:{'a' * 64}",
            "model": {
                "source": "base_model",
                "id": base_model_id,
                "family": "yolo26",
                "runtime_id": "yolo26n.pt",
                "checksum": "a" * 64,
                "revision": None,
            },
            "dataset": {
                "id": dataset_id,
                "version_id": job.resolved_snapshot["dataset"]["version_id"],
                "version": 1,
                "format": "yolo",
                "uri": f"memory://datasets/{dataset_id}/versions/1",
                "manifest_checksum": "b" * 64,
            },
            "parameters": body["params"],
            "environment": body["environment"],
            "resource_request": {"kind": "local", "gpu_count": 0},
            "allocation": {"kind": "local", "device": "0"},
            "recipe": pipeline.recipe,
            "runtime_model_id": "yolo26n.pt",
        }
        expected_snapshot = job.resolved_snapshot
        session.add(
            TrainingJobAttempt(
                training_job_id=job.id,
                attempt_number=2,
                status="failed",
                launch_spec={"adapter_key": "ultralytics.object_detection.v1"},
                launch_spec_checksum="c" * 64,
            )
        )
        session.commit()

    list_item = client.get(f"/training-jobs?pipeline_id={pipeline_id}&status=queued").json()["items"][0]
    detail_item = client.get(f"/training-jobs/{body['id']}").json()
    for item in (list_item, detail_item):
        assert item["resolved_snapshot"] == expected_snapshot
        assert [attempt["attempt_number"] for attempt in item["attempts"]] == [1, 2]


def test_training_snapshot_is_independent_from_request_and_pipeline_mutation(
    client: TestClient,
    session_factory,
):
    base_model_id, dataset_id, _ = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    request_params = {"epochs": 3, "classes": [0, 1]}

    response = client.post(
        f"/pipelines/{pipeline_id}/jobs", json={"params": request_params}
    )
    assert response.status_code == 201, response.text
    request_params["classes"].append(2)
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, pipeline_id)
        pipeline.params_template = {"epochs": 999}
        pipeline.recipe = {"model": {"runtime_id": "changed.pt"}}
        session.commit()
    with session_factory() as session:
        job = session.get(TrainingJob, response.json()["id"])
        assert job.resolved_snapshot["parameters"]["classes"] == [0, 1]
        assert job.resolved_snapshot["parameters"]["epochs"] == 3
        assert job.resolved_snapshot["runtime_model_id"] == "yolo26n.pt"


def test_create_paddlex_training_job_freezes_official_model_snapshot(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    _, dataset_id, _ = seed_training_ready_rows(session_factory)
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="paddlex-real-job",
            engine="paddlex",
            task="detect",
            scale="s",
            task_kind="object_detection",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
            model_family="PP-YOLOE",
            recipe={
                "model": {
                    "key": "pp-yoloe-s",
                    "label": "PP-YOLOE-S",
                    "runtime_id": "PP-YOLOE_plus-S",
                    "family": "PP-YOLOE",
                    "variant": "S",
                }
            },
            dataset_id=dataset_id,
            params_template={"epochs": 10},
            default_environment={"device": "remote"},
            status="ready",
        )
        session.add(pipeline)
        session.commit()
        pipeline_id = pipeline.id
    pool_id, node_ids = seed_distributed_pool(session_factory, node_count=1)

    response = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "params": {"epochs": 2, "batch_size": 4},
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 1,
                "node_ids": node_ids,
            },
        },
    )

    assert response.status_code == 201, response.text
    with session_factory() as session:
        job = session.get(TrainingJob, response.json()["id"])
        attempt = session.scalar(
            select(TrainingJobAttempt).where(
                TrainingJobAttempt.training_job_id == job.id
            )
        )
    assert job.resolved_snapshot["framework"] == "paddlex"
    assert job.resolved_snapshot["runtime_image_digest"].endswith("c" * 64)
    assert job.resolved_snapshot["runtime_model_id"] == "PP-YOLOE_plus-S"
    assert job.resolved_snapshot["model"]["revision"] == (
        "paddlex-model-zoo/3.0.3/PP-YOLOE_plus-S"
    )
    assert job.resolved_snapshot["dataset"]["manifest_checksum"] == "b" * 64
    assert job.resolved_snapshot["parameters"]["epochs"] == 2
    assert job.resolved_snapshot["parameters"]["batch_size"] == 4
    assert job.resolved_snapshot["allocation"]["world_size"] == 1
    assert attempt is not None and attempt.attempt_number == 1
    assert stream_producer.edge_commands[0].keys() == {
        "task_id",
        "task_type",
        "remote_execution_id",
    }


def test_create_distributed_training_job_persists_plan_and_enqueues_id_only_edge_command(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)

    response = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "params": {"epochs": 2},
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": list(reversed(node_ids)),
                "training_image_digest": f"registry.example/visiox/training@sha256:{'a' * 64}",
                "master_port": 29600,
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["distributed_run_id"]
    assert body["remote_execution_id"]
    assert stream_producer.commands == []
    assert stream_producer.edge_commands == [
        {
            "task_id": body["task_id"],
            "task_type": TaskType.EDGE_TRAIN,
            "remote_execution_id": body["remote_execution_id"],
        }
    ]

    with session_factory() as session:
        job = session.get(TrainingJob, body["id"])
        attempt = session.scalar(
            select(TrainingJobAttempt).where(
                TrainingJobAttempt.training_job_id == body["id"]
            )
        )
        task = session.get(Task, body["task_id"])
        run = session.get(DistributedTrainingRun, body["distributed_run_id"])
        execution = session.get(RemoteExecution, body["remote_execution_id"])
        pipeline = session.get(TrainingPipeline, pipeline_id)

    assert job.task_id == task.id
    assert job.resolved_snapshot["framework"] == "ultralytics"
    assert job.resolved_snapshot["runtime_image_digest"].endswith("a" * 64)
    assert job.resolved_snapshot["resource_request"] == {
        "kind": "distributed",
        "resource_pool_id": pool_id,
        "gpu_count": 2,
    }
    assert job.resolved_snapshot["allocation"]["world_size"] == 2
    assert job.resolved_snapshot["allocation"]["node_ids"] == sorted(node_ids)
    assert attempt is not None and attempt.attempt_number == 1
    assert pipeline.framework_locked_at is not None
    assert pipeline.first_submitted_job_id == body["id"]
    assert task.task_type == TaskType.EDGE_TRAIN.value
    assert task.payload["distributed_training_run_id"] == run.id
    assert run.training_job_id == job.id
    assert run.resource_pool_id == pool_id
    assert run.node_ids == sorted(node_ids)
    assert run.world_size == 2
    assert run.master_port == 29600
    assert (
        run.training_image_digest
        == f"registry.example/visiox/training@sha256:{'a' * 64}"
    )
    assert [rank["node_rank"] for rank in run.ranks] == [0, 1]
    assert execution.node_id == run.node_ids[0]
    assert execution.operation == "train"
    assert execution.training_job_id == job.id
    assert execution.resource_id == run.id
    detail = client.get(f"/training-jobs/{job.id}")
    assert detail.status_code == 200
    assert detail.json()["distributed_run_id"] == run.id
    assert detail.json()["remote_execution_id"] == execution.id


def test_create_llm_training_job_uses_platform_image_and_model_reference(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    with session_factory() as session:
        dataset = Dataset(
            name="llm-job-dataset",
            task="llm",
            status="validated",
            format="alpaca",
            class_schema={"format": "alpaca"},
            schema_config={"prompt": "instruction", "response": "output"},
            manifest_checksum="c" * 64,
            sample_count=8,
            annotation_count=8,
            source="llm_upload",
            storage_uri="memory://datasets/llm/train.jsonl",
        )
        session.add(dataset)
        session.flush()
        dataset_version = DatasetVersion(
            dataset_id=dataset.id,
            version=1,
            status="published",
            format="openai_messages",
            object_uri="memory://datasets/llm/versions/1/train.jsonl",
            manifest_uri="memory://datasets/llm/versions/1/manifest.json",
            manifest_checksum="c" * 64,
            source_revision="e" * 64,
            total_count=8,
            valid_count=8,
            invalid_count=0,
            skipped_count=0,
            size_bytes=128,
            schema_snapshot={},
            published_at=datetime.now(UTC),
        )
        session.add(dataset_version)
        pipeline = TrainingPipeline(
            name="llm-real-job",
            engine="llamafactory",
            task="llm",
            scale="llm",
            dataset_id=dataset.id,
            params_template={
                "model_source": "huggingface",
                "model_id": "Qwen/Qwen3-0.6B",
                "model_revision": "main",
                "resolved_revision": "d" * 40,
                "stage": "sft",
                "finetuning_type": "lora",
                "quantization_bit": 4,
                "learning_rate": 0.0001,
                "num_train_epochs": 1,
                "cutoff_len": 1024,
                "per_device_train_batch_size": 1,
                "gradient_accumulation_steps": 4,
            },
            default_environment={"device": "remote", "workers": 0},
            status="ready",
        )
        session.add(pipeline)
        session.commit()
        pipeline_id = pipeline.id
    pool_id, node_ids = seed_distributed_pool(session_factory)

    response = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "environment": {"resource_pool_id": pool_id, "node_id": node_ids[0]},
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 1,
                "node_ids": [node_ids[0]],
            },
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert len(stream_producer.edge_commands) == 1
    with session_factory() as session:
        task = session.get(Task, body["task_id"])
        run = session.get(DistributedTrainingRun, body["distributed_run_id"])
        job = session.get(TrainingJob, body["id"])
        attempt = session.scalar(
            select(TrainingJobAttempt).where(
                TrainingJobAttempt.training_job_id == body["id"]
            )
        )
        pipeline = session.get(TrainingPipeline, pipeline_id)
    assert task.payload["engine"] == "llamafactory"
    assert pipeline.framework_locked_at is not None
    assert pipeline.first_submitted_job_id == body["id"]
    assert task.payload["dataset_manifest_checksum"] == "c" * 64
    assert task.payload["dataset_version_id"] == dataset_version.id
    assert task.payload["model_reference"] == {
        "source": "huggingface",
        "model_id": "Qwen/Qwen3-0.6B",
        "revision": "d" * 40,
    }
    assert (
        run.training_image_digest
        == f"registry.example/visiox/llm-training@sha256:{'b' * 64}"
    )
    assert job.resolved_snapshot["framework"] == "llamafactory"
    assert job.resolved_snapshot["model"]["revision"] == "d" * 40
    assert job.resolved_snapshot["dataset"]["version"] == 1
    assert job.resolved_snapshot["dataset"]["manifest_checksum"] == "c" * 64
    assert job.resolved_snapshot["runtime_model_id"] == "Qwen/Qwen3-0.6B"
    assert attempt is not None and attempt.attempt_number == 1


def test_delete_pipeline_removes_distributed_training_dependencies(
    client: TestClient,
    session_factory,
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)
    created = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'a' * 64}",
            }
        },
    ).json()

    response = client.delete(f"/pipelines/{pipeline_id}")

    assert response.status_code == 204
    with session_factory() as session:
        assert session.get(TrainingPipeline, pipeline_id) is None
        assert session.get(TrainingJob, created["id"]) is None
        assert session.get(Task, created["task_id"]) is None
        assert (
            session.get(DistributedTrainingRun, created["distributed_run_id"]) is None
        )
    assert session.get(RemoteExecution, created["remote_execution_id"]) is None


def test_delete_training_record_preserves_trained_model_and_removes_task_dependencies(
    client: TestClient,
    session_factory,
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)
    created = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'c' * 64}",
            }
        },
    ).json()
    with session_factory() as session:
        job = session.get(TrainingJob, created["id"])
        task = session.get(Task, created["task_id"])
        attempt = session.scalar(
            select(TrainingJobAttempt).where(
                TrainingJobAttempt.training_job_id == job.id
            )
        )
        assert attempt is not None
        job.status = "success"
        task.status = "SUCCESS"
        model = TrainedModel(
            pipeline_id=pipeline_id,
            training_job_id=job.id,
            training_job_attempt_id=attempt.id,
            name="preserved-model",
            version="1",
            task="detect",
            artifact_role="best_weights",
            artifact_uri="memory://models/trained/best.pt",
            status="ready",
        )
        session.add_all([job, task, model])
        session.commit()
        model_id = model.id

    response = client.delete(f"/training-jobs/{created['id']}")

    assert response.status_code == 204
    with session_factory() as session:
        assert session.get(TrainingJob, created["id"]) is None
        assert session.get(Task, created["task_id"]) is None
        assert (
            session.get(DistributedTrainingRun, created["distributed_run_id"]) is None
        )
        assert session.get(RemoteExecution, created["remote_execution_id"]) is None
        preserved = session.get(TrainedModel, model_id)
        assert preserved is not None
        assert preserved.training_job_id is None
        assert preserved.training_job_attempt_id is None


def test_delete_first_training_record_repoints_pipeline_to_earliest_remaining_job(
    client: TestClient,
    session_factory,
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    created = client.post(f"/pipelines/{pipeline_id}/jobs", json={}).json()
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, pipeline_id)
        first = session.get(TrainingJob, created["id"])
        task = session.get(Task, created["task_id"])
        assert pipeline is not None and first is not None and task is not None
        locked_at = pipeline.framework_locked_at
        first.status = "success"
        task.status = "SUCCESS"
        remaining = TrainingJob(
            pipeline_id=pipeline_id,
            status="failed",
            organization_id=first.organization_id,
            owner_user_id=first.owner_user_id,
        )
        session.add(remaining)
        session.commit()
        remaining_id = remaining.id

    response = client.delete(f"/training-jobs/{created['id']}")

    assert response.status_code == 204
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, pipeline_id)
        assert pipeline is not None
        assert pipeline.first_submitted_job_id == remaining_id
        assert pipeline.framework_locked_at == locked_at


def test_delete_training_record_rejects_active_job(
    client: TestClient, session_factory
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    created = client.post(f"/pipelines/{pipeline_id}/jobs", json={}).json()

    response = client.delete(f"/training-jobs/{created['id']}")

    assert response.status_code == 409


def test_delete_training_record_allows_orphan_queued_job(
    client: TestClient,
    session_factory,
) -> None:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    created = client.post(f"/pipelines/{pipeline_id}/jobs", json={}).json()
    with session_factory() as session:
        job = session.get(TrainingJob, created["id"])
        task = session.get(Task, created["task_id"])
        assert job is not None and task is not None
        job.task_id = None
        job.status = "queued"
        session.delete(task)
        session.commit()

    response = client.delete(f"/training-jobs/{created['id']}")

    assert response.status_code == 204
    with session_factory() as session:
        assert session.get(TrainingJob, created["id"]) is None


def test_distributed_training_rejects_unavailable_requested_node(
    client: TestClient,
    session_factory,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)
    with session_factory() as session:
        node = session.get(ComputeNode, node_ids[1])
        node.status = "draining"
        session.commit()

    response = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'b' * 64}",
            }
        },
    )

    assert response.status_code == 409
    assert "available" in response.json()["detail"].lower()
    with session_factory() as session:
        assert session.scalars(select(TrainingJob)).all() == []


def test_distributed_training_requires_full_immutable_oci_image_reference(
    client: TestClient,
    session_factory,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)

    response = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"sha256:{'a' * 64}",
            }
        },
    )

    assert response.status_code == 422
    assert "immutable image digest" in response.json()["detail"]


def test_stop_distributed_training_creates_edge_stop_execution(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)
    created = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'c' * 64}",
            }
        },
    ).json()
    with session_factory() as session:
        run = session.get(DistributedTrainingRun, created["distributed_run_id"])
        job = session.get(TrainingJob, created["id"])
        run.status = "running"
        job.status = "running"
        session.commit()

    response = client.post(f"/training-jobs/{created['id']}/stop")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "stopping"
    assert body["remote_execution_id"] != created["remote_execution_id"]
    assert stream_producer.edge_commands[-1] == {
        "task_id": body["task_id"],
        "task_type": TaskType.EDGE_STOP_TRAINING,
        "remote_execution_id": body["remote_execution_id"],
    }
    with session_factory() as session:
        task = session.get(Task, body["task_id"])
        run = session.get(DistributedTrainingRun, created["distributed_run_id"])
        execution = session.get(RemoteExecution, body["remote_execution_id"])
    assert task.task_type == TaskType.EDGE_STOP_TRAINING.value
    assert run.status == "stopping"
    assert execution.operation == "stop_training"
    assert execution.resource_id == run.id


def test_resume_distributed_training_uses_checkpoint_and_increments_attempt(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)
    created = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'d' * 64}",
            }
        },
    ).json()
    checkpoint_uri = f"minio://training/checkpoints/{created['id']}/last.pt"
    checkpoint_checksum = "e" * 64
    with session_factory() as session:
        run = session.get(DistributedTrainingRun, created["distributed_run_id"])
        job = session.get(TrainingJob, created["id"])
        run.status = "stopped"
        run.checkpoint_uri = checkpoint_uri
        run.checkpoint_checksum = checkpoint_checksum
        job.status = "stopped"
        session.commit()

    response = client.post(f"/training-jobs/{created['id']}/resume", json={})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["distributed_run_id"] != created["distributed_run_id"]
    assert stream_producer.edge_commands[-1] == {
        "task_id": body["task_id"],
        "task_type": TaskType.EDGE_RESUME_TRAINING,
        "remote_execution_id": body["remote_execution_id"],
    }
    with session_factory() as session:
        resumed = session.get(DistributedTrainingRun, body["distributed_run_id"])
        execution = session.get(RemoteExecution, body["remote_execution_id"])
        attempts = list(
            session.scalars(
                select(TrainingJobAttempt)
                .where(TrainingJobAttempt.training_job_id == created["id"])
                .order_by(TrainingJobAttempt.attempt_number)
            )
        )
    assert resumed.attempt == 2
    assert resumed.resource_pool_id == pool_id
    assert resumed.node_ids == sorted(node_ids)
    assert resumed.checkpoint_uri == checkpoint_uri
    assert resumed.checkpoint_checksum == checkpoint_checksum
    assert execution.operation == "resume_training"
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert attempts[0].launch_spec != attempts[1].launch_spec
    assert attempts[0].launch_spec["env"]["VISIOX_TRAINING_ATTEMPT"] == "1"
    assert attempts[1].launch_spec["env"]["VISIOX_TRAINING_ATTEMPT"] == "2"
    for attempt_record in attempts:
        assert (
            attempt_record.launch_spec_checksum
            == LaunchSpec.model_validate(
                attempt_record.launch_spec
            ).canonical_checksum_sha256()
        )


def test_resume_distributed_training_rejects_incompatible_checkpoint_identity(
    client: TestClient,
    session_factory,
):
    base_model_id, dataset_id, _ = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)
    created = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'d' * 64}",
            }
        },
    ).json()
    with session_factory() as session:
        run = session.get(DistributedTrainingRun, created["distributed_run_id"])
        job = session.get(TrainingJob, created["id"])
        run.status = "failed"
        run.checkpoint_uri = "minio://training/checkpoints/last.pt"
        run.checkpoint_checksum = "e" * 64
        job.status = "failed"
        session.commit()

    response = client.post(
        f"/training-jobs/{created['id']}/resume",
        json={
            "checkpoint_framework": "paddlex",
            "checkpoint_model_family": "PP-YOLOE",
        },
    )

    assert response.status_code == 409
    assert "checkpoint" in response.json()["detail"].lower()
    with session_factory() as session:
        attempts = list(
            session.scalars(
                select(TrainingJobAttempt).where(
                    TrainingJobAttempt.training_job_id == created["id"]
                )
            )
        )
    assert len(attempts) == 1


def test_resume_distributed_training_rejects_unbound_checkpoint_override_without_identity(
    client: TestClient,
    session_factory,
):
    base_model_id, dataset_id, _ = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)
    created = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'d' * 64}",
            }
        },
    ).json()
    with session_factory() as session:
        run = session.get(DistributedTrainingRun, created["distributed_run_id"])
        job = session.get(TrainingJob, created["id"])
        run.status = "failed"
        run.checkpoint_uri = f"minio://training/checkpoints/{created['id']}/last.pt"
        run.checkpoint_checksum = "e" * 64
        job.status = "failed"
        session.commit()

    response = client.post(
        f"/training-jobs/{created['id']}/resume",
        json={
            "checkpoint_uri": "minio://attacker/foreign.pt",
            "checkpoint_checksum": "f" * 64,
        },
    )

    assert response.status_code == 409
    assert "checkpoint" in response.json()["detail"].lower()
    with session_factory() as session:
        attempts = list(
            session.scalars(
                select(TrainingJobAttempt).where(
                    TrainingJobAttempt.training_job_id == created["id"]
                )
            )
        )
    assert len(attempts) == 1


@pytest.mark.parametrize("failure_point", ["flush", "commit"])
def test_training_submission_database_failure_rolls_back_all_state(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
):
    base_model_id, dataset_id, _ = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    rollback_calls = 0
    original_rollback = Session.rollback

    def failing_operation(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError(f"injected {failure_point} failure")

    def recording_rollback(self, *args, **kwargs):
        nonlocal rollback_calls
        rollback_calls += 1
        return original_rollback(self, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(Session, failure_point, failing_operation)
        patcher.setattr(Session, "rollback", recording_rollback)
        with pytest.raises(RuntimeError, match=f"injected {failure_point} failure"):
            client.post(f"/pipelines/{pipeline_id}/jobs", json={})

    assert rollback_calls >= 1
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, pipeline_id)
        jobs = list(
            session.scalars(
                select(TrainingJob).where(TrainingJob.pipeline_id == pipeline_id)
            )
        )
        attempts = list(session.scalars(select(TrainingJobAttempt)))
    assert jobs == []
    assert attempts == []
    assert pipeline.status == "ready"
    assert pipeline.framework_locked_at is None
    assert pipeline.first_submitted_job_id is None


@pytest.mark.parametrize(
    ("failure_point", "failing_flush_number"),
    [("flush", 2), ("flush", 3), ("flush", 4), ("commit", None)],
)
def test_distributed_training_submission_database_failure_rolls_back_all_state(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
    failing_flush_number: int | None,
):
    base_model_id, dataset_id, _ = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory)
    rollback_calls = 0
    flush_calls = 0
    original_flush = Session.flush
    original_rollback = Session.rollback

    def failing_flush(self, *args, **kwargs):
        nonlocal flush_calls
        flush_calls += 1
        if flush_calls == failing_flush_number:
            raise RuntimeError(f"injected distributed flush {flush_calls} failure")
        return original_flush(self, *args, **kwargs)

    def failing_commit(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError("injected distributed commit failure")

    def recording_rollback(self, *args, **kwargs):
        nonlocal rollback_calls
        rollback_calls += 1
        return original_rollback(self, *args, **kwargs)

    with monkeypatch.context() as patcher:
        if failure_point == "flush":
            patcher.setattr(Session, "flush", failing_flush)
            expected = rf"injected distributed flush {failing_flush_number} failure"
        else:
            patcher.setattr(Session, "commit", failing_commit)
            expected = "injected distributed commit failure"
        patcher.setattr(Session, "rollback", recording_rollback)
        with pytest.raises(RuntimeError, match=expected):
            client.post(
                f"/pipelines/{pipeline_id}/jobs",
                json={
                    "distributed": {
                        "resource_pool_id": pool_id,
                        "requested_gpus": 2,
                        "node_ids": node_ids,
                        "training_image_digest": f"registry.example/visiox/training@sha256:{'d' * 64}",
                    }
                },
            )

    assert rollback_calls >= 1
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, pipeline_id)
        jobs = list(
            session.scalars(
                select(TrainingJob).where(TrainingJob.pipeline_id == pipeline_id)
            )
        )
        tasks = list(
            session.scalars(select(Task).where(Task.resource_type == "training_job"))
        )
        attempts = list(session.scalars(select(TrainingJobAttempt)))
        runs = list(session.scalars(select(DistributedTrainingRun)))
        executions = list(session.scalars(select(RemoteExecution)))
    assert jobs == []
    assert tasks == []
    assert attempts == []
    assert runs == []
    assert executions == []
    assert pipeline.status == "ready"
    assert pipeline.framework_locked_at is None
    assert pipeline.first_submitted_job_id is None


def test_resume_distributed_training_rejects_pool_change(
    client: TestClient,
    session_factory,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    pool_id, node_ids = seed_distributed_pool(session_factory, name="original-pool")
    other_pool_id, other_node_ids = seed_distributed_pool(
        session_factory, name="other-pool"
    )
    created = client.post(
        f"/pipelines/{pipeline_id}/jobs",
        json={
            "distributed": {
                "resource_pool_id": pool_id,
                "requested_gpus": 2,
                "node_ids": node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'f' * 64}",
            }
        },
    ).json()
    with session_factory() as session:
        run = session.get(DistributedTrainingRun, created["distributed_run_id"])
        run.status = "failed"
        run.checkpoint_uri = "minio://training/checkpoints/last.pt"
        run.checkpoint_checksum = "1" * 64
        session.commit()

    response = client.post(
        f"/training-jobs/{created['id']}/resume",
        json={
            "distributed": {
                "resource_pool_id": other_pool_id,
                "requested_gpus": 2,
                "node_ids": other_node_ids,
                "training_image_digest": f"registry.example/visiox/training@sha256:{'f' * 64}",
            }
        },
    )

    assert response.status_code == 409
    assert "resource pool" in response.json()["detail"].lower()


def test_cancel_training_task_marks_job_and_pipeline_canceled(
    client: TestClient,
    session_factory,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    job = client.post(f"/pipelines/{pipeline_id}/jobs", json={}).json()

    response = client.post(f"/tasks/{job['task_id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.CANCELED.value
    with session_factory() as session:
        saved_job = session.get(TrainingJob, job["id"])
        saved_pipeline = session.get(TrainingPipeline, pipeline_id)
        saved_task = session.get(Task, job["task_id"])
    assert saved_job.status == "canceled"
    assert saved_pipeline.status == "canceled"
    assert saved_task.error_code == "TRAINING_CANCELED"


def test_cancel_training_task_keeps_pipeline_running_for_active_peer_job(
    client: TestClient,
    session_factory,
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    job = client.post(f"/pipelines/{pipeline_id}/jobs", json={}).json()
    with session_factory() as session:
        session.add(TrainingJob(pipeline_id=pipeline_id, status="running"))
        session.commit()

    response = client.post(f"/tasks/{job['task_id']}/cancel")

    assert response.status_code == 200
    with session_factory() as session:
        assert session.get(TrainingPipeline, pipeline_id).status == "running"  # type: ignore[union-attr]


def test_create_training_job_revalidates_pipeline_resources(
    client: TestClient, session_factory
):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
    pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
    with session_factory() as session:
        dataset = session.get(Dataset, dataset_id)
        dataset.status = "created"
        session.add(dataset)
        session.commit()

    response = client.post(f"/pipelines/{pipeline_id}/jobs", json={})

    assert response.status_code == 409
    with session_factory() as session:
        jobs = session.scalars(select(TrainingJob)).all()
    assert jobs == []


def test_create_training_job_marks_job_and_task_failed_when_enqueue_fails(
    session_factory,
):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_session] = override_session
    app.dependency_overrides[get_training_job_session] = override_session
    app.dependency_overrides[get_training_stream_producer] = lambda: (
        FailingStreamProducer()
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        ultralytics_training_image_digest=f"registry.example/visiox/ultralytics-training@sha256:{'a' * 64}",
    )
    with TestClient(app) as client:
        base_model_id, dataset_id, _sample_id = seed_training_ready_rows(
            session_factory
        )
        pipeline_id = create_pipeline(client, base_model_id, dataset_id).json()["id"]
        response = client.post(f"/pipelines/{pipeline_id}/jobs", json={})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"

    with session_factory() as session:
        job = session.get(TrainingJob, body["id"])
        task = session.get(Task, body["task_id"])
        pipeline = session.get(TrainingPipeline, pipeline_id)

    assert job.status == "failed"
    assert pipeline.status == "failed"
    assert task.status == TaskStatus.FAILED.value
    assert task.error_code == "ENQUEUE_FAILED"


def _create_job_for_worker(
    session_factory, tmp_path, storage: InMemoryObjectStorageClient
) -> tuple[str, str]:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(
        session_factory, storage, tmp_path
    )
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="worker-pipeline",
            task="detect",
            scale="n",
            base_model_id=base_model_id,
            dataset_id=dataset_id,
            params_template={"epochs": 2, "batch": 4, "imgsz": 640},
            default_environment={"device": "cpu"},
            status="ready",
        )
        session.add(pipeline)
        session.flush()
        job = TrainingJob(
            pipeline_id=pipeline.id,
            status="queued",
            params={
                "epochs": 2,
                "batch": 4,
                "imgsz": 640,
                "device": "cpu",
                "optimizer": "MuSGD",
                "cos_lr": True,
                "classes": [0, 1],
            },
        )
        task = Task(
            task_type=TaskType.TRAIN_MODEL.value,
            status=TaskStatus.QUEUED.value,
            resource_type="training_job",
            payload={},
        )
        session.add_all([job, task])
        session.flush()
        job.task_id = task.id
        task.resource_id = job.id
        task.payload = {
            "pipeline_id": pipeline.id,
            "training_job_id": job.id,
            "dataset_id": dataset_id,
            "base_model_id": base_model_id,
            "params": job.params,
            "environment": {"device": "cpu"},
        }
        session.commit()
        return task.id, job.id


def test_worker_runs_training_exports_dataset_and_registers_model(
    session_factory, tmp_path
):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    runner = FakeRunner()

    with session_factory() as session:
        result = run_training_job(
            session, storage, runner, task_id, job_id, tmp_path / "work"
        )

    assert result.training_job_id == job_id
    assert result.trained_model_id
    assert len(runner.commands) == 1
    assert runner.commands[0][1:3] == ["-m", "visiox_training_worker.train_entrypoint"]
    assert "optimizer=MuSGD" in runner.commands[0]
    assert "cos_lr=True" in runner.commands[0]
    assert "classes=[0,1]" in runner.commands[0]
    assert "device=cpu" in runner.commands[0]
    assert not isinstance(runner.commands[0], str)
    data_arg = next(
        argument for argument in runner.commands[0] if argument.startswith("data=")
    )
    data_yaml_path = Path(data_arg.split("=", 1)[1])
    assert (
        f"path: {data_yaml_path.parent.resolve().as_posix()}"
        in data_yaml_path.read_text(encoding="utf-8")
    )

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        trained_model = session.get(TrainedModel, result.trained_model_id)
        pipeline = session.get(TrainingPipeline, job.pipeline_id)
        trained_models = session.scalars(
            select(TrainedModel).where(TrainedModel.pipeline_id == pipeline.id)
        ).all()
        log_stream = session.scalar(
            select(LogStream).where(
                LogStream.resource_type == "training_job",
                LogStream.resource_id == job_id,
            )
        )

    assert job.status == "success"
    assert pipeline.status == "success"
    assert job.trained_model_id == trained_model.id
    assert trained_model.name == "best.pt"
    assert job.metrics["mAP50"] == 0.91
    assert job.metrics["precision"] == 0.88
    assert job.metrics["observability"]["mlflow_run_name"] == f"job-{job_id}"
    assert set(job.metrics["weights"]) == {"best.pt", "last.pt"}
    assert job.log_uri.startswith("memory://training/")
    assert log_stream is not None
    assert log_stream.status == "completed"
    assert log_stream.line_count == 1
    assert log_stream.redacted_log_uri.startswith("memory://logs/")
    assert task.status == TaskStatus.SUCCESS.value
    assert task.progress == 100
    assert {model.name for model in trained_models} == {"best.pt", "last.pt"}
    assert {model.name: model.metrics["checksum"] for model in trained_models} == {
        "best.pt": sha256(b"best model").hexdigest(),
        "last.pt": sha256(b"last model").hexdigest(),
    }
    weight_objects = {
        object_name.split("/")[-1]: payload
        for (bucket, object_name), payload in storage.objects.items()
        if bucket == "models" and object_name.startswith("trained/")
    }
    assert weight_objects == {"best.pt": b"best model", "last.pt": b"last model"}


def test_training_job_artifacts_can_be_listed_and_downloaded(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    best_path = tmp_path / "best.pt"
    result_path = tmp_path / "results.png"
    best_path.write_bytes(b"best model")
    result_path.write_bytes(b"training chart")
    best_uri = storage.put_file("models", "trained/model-1/best.pt", best_path)
    result_uri = storage.put_file(
        "training", "jobs/job-1/visualizations/results.png", result_path
    )

    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="artifact-pipeline", task="detect", scale="n", status="success"
        )
        session.add(pipeline)
        session.flush()
        job = TrainingJob(
            id="job-1",
            pipeline_id=pipeline.id,
            status="success",
            metrics={
                "weights": {"best.pt": best_uri},
                "visualizations": {"results.png": result_uri},
            },
        )
        session.add(job)
        session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_training_job_session] = override_session
    app.dependency_overrides[get_training_object_storage_client] = lambda: storage
    with TestClient(app) as client:
        response = client.get("/training-jobs/job-1/artifacts")
        download = client.get("/training-jobs/job-1/artifacts/weight/best.pt")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "name": "best.pt",
                "kind": "weight",
                "size_bytes": 10,
                "download_url": "/training-jobs/job-1/artifacts/weight/best.pt",
            },
            {
                "name": "results.png",
                "kind": "visualization",
                "size_bytes": 14,
                "download_url": "/training-jobs/job-1/artifacts/visualization/results.png",
            },
        ]
    }
    assert download.status_code == 200
    assert download.content == b"best model"
    assert download.headers["content-disposition"].endswith('filename="best.pt"')


def test_training_job_artifacts_use_stored_filename_for_legacy_model_rows(
    session_factory, tmp_path
):
    storage = InMemoryObjectStorageClient()
    weight_path = tmp_path / "best.pt"
    weight_path.write_bytes(b"legacy best model")
    weight_uri = storage.put_file("models", "trained/model-legacy/best.pt", weight_path)

    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="legacy-artifact-pipeline", task="detect", scale="n", status="success"
        )
        session.add(pipeline)
        session.flush()
        job = TrainingJob(
            id="job-legacy", pipeline_id=pipeline.id, status="success", metrics={}
        )
        session.add(job)
        session.flush()
        model = TrainedModel(
            pipeline_id=pipeline.id,
            training_job_id=job.id,
            name="legacy pipeline display name",
            version="legacy-version",
            task="detect",
            artifact_uri=weight_uri,
            metrics={},
            status="ready",
        )
        session.add(model)
        session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_training_job_session] = override_session
    app.dependency_overrides[get_training_object_storage_client] = lambda: storage
    with TestClient(app) as client:
        response = client.get("/training-jobs/job-legacy/artifacts")

    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "best.pt"


def test_subprocess_runner_collects_ultralytics_metrics_and_visualizations(tmp_path):
    command = tmp_path / "fake-yolo.py"
    command.write_text(
        """
from pathlib import Path
project = next(arg.split("=", 1)[1] for arg in __import__("sys").argv if arg.startswith("project="))
name = next(arg.split("=", 1)[1] for arg in __import__("sys").argv if arg.startswith("name="))
run = Path(project) / name
(run / "weights").mkdir(parents=True)
(run / "weights" / "best.pt").write_bytes(b"model")
(run / "weights" / "last.pt").write_bytes(b"last")
(run / "results.csv").write_text("epoch,metrics/mAP50(B),metrics/precision(B)\\n1,0.91,0.88\\n", encoding="utf-8")
(run / "results.png").write_bytes(b"png")
""",
        encoding="utf-8",
    )

    result = SubprocessTrainingRunner().run(
        [
            "python",
            str(command),
            f"project={tmp_path / 'runs'}",
            "name=job-test",
        ],
        tmp_path,
    )

    assert result.exit_code == 0
    assert (
        result.artifact_path == tmp_path / "runs" / "job-test" / "weights" / "best.pt"
    )
    assert result.weight_paths == {
        "best.pt": tmp_path / "runs" / "job-test" / "weights" / "best.pt",
        "last.pt": tmp_path / "runs" / "job-test" / "weights" / "last.pt",
    }
    assert result.metrics == {
        "epoch": 1,
        "metrics/mAP50(B)": 0.91,
        "metrics/precision(B)": 0.88,
    }
    assert result.visualization_paths == {
        "results.png": tmp_path / "runs" / "job-test" / "results.png"
    }


def test_worker_success_keeps_pipeline_running_for_active_peer_job(
    session_factory, tmp_path
):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        session.add(TrainingJob(pipeline_id=job.pipeline_id, status="running"))
        session.commit()

    with session_factory() as session:
        run_training_job(session, storage, FakeRunner(), task_id, job_id, tmp_path / "work")

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        assert session.get(TrainingPipeline, job.pipeline_id).status == "running"


def test_worker_falls_back_to_cpu_when_cuda_device_requested_without_cuda(
    session_factory, tmp_path, monkeypatch
):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    monkeypatch.setattr(training_worker_main, "_cuda_is_available", lambda: False)
    runner = FakeRunner()

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        job.params = {**job.params, "device": "0"}
        task.payload = {
            **task.payload,
            "params": job.params,
            "environment": {"device": "0"},
        }
        session.add_all([job, task])
        session.commit()

    with session_factory() as session:
        run_training_job(session, storage, runner, task_id, job_id, tmp_path / "work")

    assert "device=cpu" in runner.commands[0]
    assert "device=0" not in runner.commands[0]


def test_worker_marks_task_and_job_failed_when_runner_fails(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="cuda out of memory"):
            run_training_job(
                session, storage, FailingRunner(), task_id, job_id, tmp_path / "work"
            )

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        pipeline = session.get(TrainingPipeline, job.pipeline_id)
        trained_models = session.scalars(select(TrainedModel)).all()

    assert job.status == "failed"
    assert pipeline.status == "failed"
    assert task.status == TaskStatus.FAILED.value
    assert task.stage == "train"
    assert task.error_code == "TRAINING_FAILED"
    assert "cuda out of memory" in task.error_message
    assert task.retryable is True
    assert trained_models == []
    assert not any(
        bucket == "models" and object_name.startswith("trained/")
        for bucket, object_name in storage.objects
    )


def test_worker_failure_keeps_pipeline_running_for_active_peer_job(
    session_factory, tmp_path
):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        session.add(TrainingJob(pipeline_id=job.pipeline_id, status="running"))
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="cuda out of memory"):
            run_training_job(
                session, storage, FailingRunner(), task_id, job_id, tmp_path / "work"
            )

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        assert session.get(TrainingPipeline, job.pipeline_id).status == "running"


def test_worker_marks_pipeline_canceled_when_training_is_canceled(
    session_factory, tmp_path
):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="training canceled"):
            run_training_job(
                session,
                storage,
                CancelingRunner(session_factory, task_id),
                task_id,
                job_id,
                tmp_path / "work",
            )

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        pipeline = session.get(TrainingPipeline, job.pipeline_id)

    assert job.status == "canceled"
    assert pipeline.status == "canceled"
    assert task.status == TaskStatus.CANCELED.value
    assert task.error_code == "TRAINING_CANCELED"
    assert task.retryable is False


def test_run_pending_training_tasks_keeps_worker_alive_when_task_fails(
    session_factory, tmp_path
):
    storage = InMemoryObjectStorageClient()
    task_id, _job_id = _create_job_for_worker(session_factory, tmp_path, storage)

    with session_factory() as session:
        processed = run_pending_training_tasks(
            session, storage, FailingRunner(), tmp_path / "work"
        )

    assert [task.id for task in processed] == [task_id]
    assert processed[0].status == TaskStatus.FAILED.value
    assert processed[0].error_code == "TRAINING_FAILED"


def test_worker_rejects_payload_mismatch_and_marks_failed(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    runner = FakeRunner()
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.payload = {**task.payload, "dataset_id": "wrong-dataset"}
        session.add(task)
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="dataset_id"):
            run_training_job(
                session, storage, runner, task_id, job_id, tmp_path / "work"
            )

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)

    assert runner.commands == []
    assert job.status == "failed"
    assert task.status == TaskStatus.FAILED.value
    assert task.error_code == "INVALID_TASK_PAYLOAD"


def test_worker_does_not_run_when_task_is_already_claimed(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    runner = FakeRunner()
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.RUNNING.value
        session.add(task)
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="already claimed"):
            run_training_job(
                session, storage, runner, task_id, job_id, tmp_path / "work"
            )

    assert runner.commands == []


def test_worker_uses_safe_base_model_download_filename(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(
        session_factory,
        storage,
        tmp_path,
        base_filename="../escape.pt",
    )
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="safe-filename-pipeline",
            task="detect",
            scale="n",
            base_model_id=base_model_id,
            dataset_id=dataset_id,
            params_template={"epochs": 2, "batch": 4, "imgsz": 640},
            default_environment={"device": "cpu"},
            status="ready",
        )
        session.add(pipeline)
        session.flush()
        job = TrainingJob(
            pipeline_id=pipeline.id,
            status="queued",
            params={"epochs": 2, "batch": 4, "imgsz": 640},
        )
        task = Task(
            task_type=TaskType.TRAIN_MODEL.value,
            status=TaskStatus.QUEUED.value,
            resource_type="training_job",
            payload={},
        )
        session.add_all([job, task])
        session.flush()
        job.task_id = task.id
        task.resource_id = job.id
        task.payload = {
            "pipeline_id": pipeline.id,
            "training_job_id": job.id,
            "dataset_id": dataset_id,
            "base_model_id": base_model_id,
            "params": job.params,
            "environment": {},
        }
        session.commit()
        task_id = task.id
        job_id = job.id

    with session_factory() as session:
        run_training_job(
            session, storage, FakeRunner(), task_id, job_id, tmp_path / "work"
        )

    assert (tmp_path / "work" / "base-model" / "base.pt").exists()
    assert not (tmp_path / "work" / "escape.pt").exists()


def test_worker_replaces_placeholder_base_model_with_real_ultralytics_asset(
    session_factory, tmp_path, monkeypatch
):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    storage.objects[("models", "base/detect-n.pt")] = (
        b"visiox prepared yolo26 base model placeholder: yolo26-detect-n\n"
    )
    downloaded = tmp_path / "downloaded.pt"
    downloaded.write_bytes(b"real ultralytics weights")

    def fake_download(filename: str, target_dir: Path) -> Path:
        assert filename.endswith(".pt")
        assert target_dir.name == "base-model"
        return downloaded

    monkeypatch.setattr(
        training_worker_main, "_download_base_model_asset", fake_download
    )
    runner = InspectingRunner()

    with session_factory() as session:
        run_training_job(session, storage, runner, task_id, job_id, tmp_path / "work")

    assert runner.base_model_bytes == b"real ultralytics weights"
    assert (
        storage.objects[("models", "base/detect-n.pt")] == b"real ultralytics weights"
    )


def test_worker_cleans_uploaded_artifacts_when_later_persist_fails(
    session_factory, tmp_path
):
    storage = FailingLogStorage()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="log upload failed"):
            run_training_job(
                session, storage, FakeRunner(), task_id, job_id, tmp_path / "work"
            )

    assert not any(
        bucket == "models" and object_name.startswith("trained/")
        for bucket, object_name in storage.objects
    )
    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        trained_models = session.scalars(select(TrainedModel)).all()

    assert job.status == "failed"
    assert task.status == TaskStatus.FAILED.value
    assert trained_models == []


def test_worker_is_idempotent_for_successful_training_job(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    runner = FakeRunner()

    with session_factory() as session:
        first = run_training_job(
            session, storage, runner, task_id, job_id, tmp_path / "work"
        )
    with session_factory() as session:
        second = run_training_job(
            session, storage, runner, task_id, job_id, tmp_path / "work-again"
        )

    assert second.trained_model_id == first.trained_model_id
    assert len(runner.commands) == 1


def test_trained_model_training_job_id_is_unique(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    _task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    with session_factory() as session:
        first = TrainedModel(
            training_job_id=job_id,
            name="first",
            version="v1",
            task="detect",
            artifact_uri="memory://models/trained/first/best.pt",
            status="ready",
        )
        duplicate = TrainedModel(
            training_job_id=job_id,
            name="duplicate",
            version="v2",
            task="detect",
            artifact_uri="memory://models/trained/duplicate/best.pt",
            status="ready",
        )
        session.add(first)
        session.commit()
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    ("framework", "adapter_key", "task", "role", "path", "display_name"),
    [
        (
            "ultralytics",
            "ultralytics.object_detection.v1",
            "detect",
            "best_weights",
            "runs/exp/weights/champion.ckpt",
            "Best weights",
        ),
        (
            "paddlex",
            "paddlex.object_detection.v1",
            "detect",
            "best_dynamic_weights",
            "output/best_model/model.pdparams",
            "Best dynamic weights",
        ),
        (
            "llamafactory",
            "llamafactory.llm_sft.v1",
            "llm_sft",
            "adapter_weights",
            "adapter/final-adapter.safetensors",
            "Adapter weights",
        ),
    ],
)
def test_framework_artifact_manifests_create_typed_trained_models(
    session_factory,
    framework,
    adapter_key,
    task,
    role,
    path,
    display_name,
):
    from visiox_api.services.training_artifacts import ingest_training_artifacts

    checksum = sha256(f"{framework}-artifact".encode()).hexdigest()
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name=f"{framework}-artifact-pipeline",
            task=task,
            scale="n",
            framework=framework,
            adapter_key=adapter_key,
            adapter_version="1.0.0",
            model_family=f"{framework}-family",
            status="running",
        )
        session.add(pipeline)
        session.flush()
        job = TrainingJob(pipeline_id=pipeline.id, status="artifact_collecting")
        session.add(job)
        session.flush()
        attempt = TrainingJobAttempt(
            training_job_id=job.id,
            attempt_number=1,
            status="artifact_collecting",
            launch_spec={"adapter_key": adapter_key},
            launch_spec_checksum="a" * 64,
        )
        session.add(attempt)
        session.flush()
        result = ingest_training_artifacts(
            session,
            job=job,
            pipeline=pipeline,
            attempt=attempt,
            manifest={
                "schema_version": "1.0",
                "task_id": job.id,
                "adapter_key": adapter_key,
                "adapter_version": "1.0.0",
                "checksum_sha256": "b" * 64,
                "artifacts": [
                    {
                        "path": path,
                        "size_bytes": 17,
                        "checksum_sha256": checksum,
                        "artifact_type": "model_weight",
                    }
                ],
            },
            artifact_uris={
                path: f"minio://models/trained/{job.id}/attempt-1/{path}"
            },
            artifact_roles={path: role},
        )
        session.commit()

        model = session.get(TrainedModel, result.best_model_id)

    assert model is not None
    assert model.name == Path(path).name
    assert model.display_name == display_name
    assert model.framework == framework
    assert model.adapter_key == adapter_key
    assert model.model_family == f"{framework}-family"
    assert model.artifact_role == role
    assert model.checksum == checksum
    assert model.size_bytes == 17
    assert model.artifact_uri.endswith(path)


def test_generic_model_weight_requires_explicit_artifact_role(session_factory):
    from visiox_api.services.training_artifacts import (
        ArtifactCollectionError,
        ingest_training_artifacts,
    )

    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="explicit-role-pipeline",
            task="detect",
            scale="n",
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
            adapter_version="1.0.0",
        )
        session.add(pipeline)
        session.flush()
        job = TrainingJob(pipeline_id=pipeline.id, status="artifact_collecting")
        session.add(job)
        session.flush()
        attempt = TrainingJobAttempt(
            training_job_id=job.id,
            attempt_number=1,
            status="artifact_collecting",
            launch_spec={},
            launch_spec_checksum="a" * 64,
        )
        session.add(attempt)
        session.flush()
        path = "runs/job/weights/best.pt"

        with pytest.raises(ArtifactCollectionError, match="deployable best artifact"):
            ingest_training_artifacts(
                session,
                job=job,
                pipeline=pipeline,
                attempt=attempt,
                manifest={
                    "task_id": job.id,
                    "adapter_key": pipeline.adapter_key,
                    "adapter_version": pipeline.adapter_version,
                    "artifacts": [
                        {
                            "path": path,
                            "size_bytes": 4,
                            "checksum_sha256": "b" * 64,
                            "artifact_type": "model_weight",
                        }
                    ],
                },
                artifact_uris={path: f"minio://models/{path}"},
            )


def test_artifact_manifest_reconciliation_is_idempotent_and_attempt_scoped(
    session_factory,
):
    from visiox_api.services.training_artifacts import ingest_training_artifacts

    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="attempt-artifact-pipeline",
            task="detect",
            scale="n",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
            model_family="PP-YOLOE-S",
            status="running",
        )
        session.add(pipeline)
        session.flush()
        job = TrainingJob(pipeline_id=pipeline.id, status="artifact_collecting")
        session.add(job)
        session.flush()
        attempts = [
            TrainingJobAttempt(
                training_job_id=job.id,
                attempt_number=number,
                status="artifact_collecting",
                launch_spec={"adapter_key": pipeline.adapter_key},
                launch_spec_checksum=str(number) * 64,
            )
            for number in (1, 2)
        ]
        session.add_all(attempts)
        session.flush()
        path = "best_model/model.pdparams"
        manifest = {
            "schema_version": "1.0",
            "task_id": job.id,
            "adapter_key": pipeline.adapter_key,
            "adapter_version": pipeline.adapter_version,
            "checksum_sha256": "c" * 64,
            "artifacts": [
                {
                    "path": path,
                    "size_bytes": 9,
                    "checksum_sha256": "d" * 64,
                    "artifact_type": "best_dynamic_weights",
                }
            ],
        }
        first = ingest_training_artifacts(
            session,
            job=job,
            pipeline=pipeline,
            attempt=attempts[0],
            manifest=manifest,
            artifact_uris={path: f"minio://models/trained/{job.id}/attempt-1/{path}"},
        )
        repeated = ingest_training_artifacts(
            session,
            job=job,
            pipeline=pipeline,
            attempt=attempts[0],
            manifest=manifest,
            artifact_uris={path: f"minio://models/trained/{job.id}/attempt-1/{path}"},
        )
        second = ingest_training_artifacts(
            session,
            job=job,
            pipeline=pipeline,
            attempt=attempts[1],
            manifest=manifest,
            artifact_uris={path: f"minio://models/trained/{job.id}/attempt-2/{path}"},
        )
        session.commit()
        models = session.scalars(
            select(TrainedModel).order_by(TrainedModel.training_job_attempt_id)
        ).all()

    assert first.best_model_id == repeated.best_model_id
    assert second.best_model_id != first.best_model_id
    assert len(models) == 2
    assert {model.training_job_attempt_id for model in models} == {
        attempts[0].id,
        attempts[1].id,
    }
    assert {model.artifact_uri for model in models} == {
        f"minio://models/trained/{job.id}/attempt-1/{path}",
        f"minio://models/trained/{job.id}/attempt-2/{path}",
    }


def test_artifact_ingestion_rechecks_winner_after_unique_insert_conflict():
    from visiox_api.services.training_artifacts import ingest_training_artifacts

    path = "best_model/model.pdparams"
    artifact_uri = f"minio://models/trained/job-race/attempt-1/{path}"
    winner = SimpleNamespace(
        id="model-winner",
        artifact_role="best_dynamic_weights",
        artifact_uri=artifact_uri,
        checksum="d" * 64,
        size_bytes=9,
    )

    class Savepoint:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class RacingSession:
        def __init__(self):
            self.scalar_calls = 0

        def scalar(self, _statement):
            self.scalar_calls += 1
            return None if self.scalar_calls == 1 else winner

        def begin_nested(self):
            return Savepoint()

        def add(self, _value):
            return None

        def flush(self):
            raise IntegrityError("insert", {}, RuntimeError("unique conflict"))

    session = RacingSession()
    pipeline = SimpleNamespace(
        id="pipeline-race",
        organization_id=None,
        owner_user_id=None,
        task="detect",
        framework="paddlex",
        adapter_key="paddlex.object_detection.v1",
        adapter_version="1.0.0",
        model_family="PP-YOLOE-S",
    )
    job = SimpleNamespace(
        id="job-race",
        pipeline_id=pipeline.id,
        organization_id=None,
        owner_user_id=None,
    )
    attempt = SimpleNamespace(id="attempt-race", attempt_number=1)
    manifest = {
        "task_id": job.id,
        "adapter_key": pipeline.adapter_key,
        "adapter_version": pipeline.adapter_version,
        "artifacts": [
            {
                "path": path,
                "size_bytes": 9,
                "checksum_sha256": "d" * 64,
                "artifact_type": "best_dynamic_weights",
            }
        ],
    }

    result = ingest_training_artifacts(
        session,  # type: ignore[arg-type]
        job=job,  # type: ignore[arg-type]
        pipeline=pipeline,  # type: ignore[arg-type]
        attempt=attempt,  # type: ignore[arg-type]
        manifest=manifest,
        artifact_uris={path: artifact_uri},
    )

    assert result.model_ids == (winner.id,)
    assert result.best_model_id == winner.id
    assert session.scalar_calls == 2


def test_pipeline_status_is_derived_from_active_job_attempt(session_factory):
    from visiox_api.services.training_artifacts import sync_pipeline_status

    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="derived-status-pipeline", task="detect", scale="n", status="failed"
        )
        session.add(pipeline)
        session.flush()
        old_job = TrainingJob(pipeline_id=pipeline.id, status="failed")
        active_job = TrainingJob(pipeline_id=pipeline.id, status="running")
        session.add_all([old_job, active_job])
        session.flush()
        active_attempt = TrainingJobAttempt(
            training_job_id=active_job.id,
            attempt_number=2,
            status="evaluating",
            launch_spec={},
            launch_spec_checksum="a" * 64,
        )
        session.add(active_attempt)
        session.flush()

        assert sync_pipeline_status(session, pipeline) == "evaluating"
        active_attempt.status = "artifact_collecting"
        session.flush()
        assert sync_pipeline_status(session, pipeline) == "artifact_collecting"
        active_attempt.status = "succeeded"
        active_job.status = "success"
        session.flush()
        assert sync_pipeline_status(session, pipeline) == "success"
