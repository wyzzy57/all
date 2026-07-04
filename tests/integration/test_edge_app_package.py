from __future__ import annotations

import hashlib
import json
import tarfile
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.edge_apps import get_edge_app_session, get_edge_app_stream_producer
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import EdgeApp, EdgeAppVersion, Task, TrainedModel
from visiox_storage.client import InMemoryObjectStorageClient
from visiox_training_worker.dispatcher import TrainingWorkerRunners, dispatch_training_worker_task
from visiox_training_worker.export_flow import ExportCommandResult, run_edge_app_packaging
from visiox_yolo26.edge_app import EdgeAppPackageSpec, build_edge_app_package
from visiox_yolo26.export import ExportParamsError, build_export_command


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


class FailingStreamProducer:
    async def enqueue(self, command):
        raise RuntimeError("redis unavailable")


class FakeExportRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, argv: list[str], work_dir: Path) -> ExportCommandResult:
        self.commands.append(argv)
        export_format = "onnx"
        for arg in argv:
            if arg.startswith("format="):
                export_format = arg.removeprefix("format=")
        artifact = work_dir / "export" / f"exported.{export_format}"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"exported model")
        return ExportCommandResult(exit_code=0, stdout="export ok", artifact_path=artifact)


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-edge-app.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def stream_producer() -> FakeStreamProducer:
    return FakeStreamProducer()


@pytest.fixture()
def client(session_factory, stream_producer: FakeStreamProducer) -> Generator[TestClient]:
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_edge_app_session] = override_session
    app.dependency_overrides[get_edge_app_stream_producer] = lambda: stream_producer

    with TestClient(app) as test_client:
        yield test_client


def seed_ready_trained_model(
    session_factory,
    storage: InMemoryObjectStorageClient | None = None,
    tmp_path: Path | None = None,
) -> str:
    with session_factory() as session:
        model = TrainedModel(
            name="trained-detect",
            version="v1",
            task="detect",
            artifact_uri="memory://models/trained/model/best.pt",
            metrics={"mAP50": 0.9},
            status="ready",
        )
        session.add(model)
        session.commit()
        model_id = model.id

    if storage is not None and tmp_path is not None:
        artifact = tmp_path / "best.pt"
        artifact.write_bytes(b"trained model")
        storage.put_file("models", "trained/model/best.pt", artifact)

    return model_id


def test_build_export_command_validates_params(tmp_path):
    command = build_export_command(
        model_path=tmp_path / "best.pt",
        format="ONNX",
        output_dir=tmp_path / "export",
        imgsz=640,
        half=True,
        device="cpu",
    )

    assert command.argv == [
        "yolo",
        "export",
        f"model={tmp_path / 'best.pt'}",
        "format=onnx",
        f"project={tmp_path / 'export'}",
        "imgsz=640",
        "half=True",
        "device=cpu",
    ]
    with pytest.raises(ExportParamsError):
        build_export_command(tmp_path / "best.pt", "bad", tmp_path / "export")
    with pytest.raises(ExportParamsError):
        build_export_command(tmp_path / "best.pt", "onnx", tmp_path / "export", imgsz=0)


def test_build_edge_app_package_writes_expected_tar_entries(tmp_path):
    model_path = tmp_path / "exported.onnx"
    model_path.write_bytes(b"onnx model")
    result = build_edge_app_package(
        EdgeAppPackageSpec(
            app_name="quality-line",
            version="v1",
            task="detect",
            model_format="onnx",
            model_path=model_path,
            runtime={"device": "cpu", "confidence": 0.25},
            cameras=[{"camera_id": "cam-1", "rtsp_url": "rtsp://example"}],
            rules={"classes": ["defect"]},
            image_ref="registry.local/visiox/yolo26-inference:0.1.0",
            output_path=tmp_path / "package.tar.gz",
        )
    )

    assert result.checksum == hashlib.sha256(result.package_path.read_bytes()).hexdigest()
    with tarfile.open(result.package_path, "r:gz") as tar:
        names = set(tar.getnames())
        runtime = json.loads(tar.extractfile("config/runtime.yaml").read().decode("utf-8"))
        image_ref = tar.extractfile("services/yolo26-inference/image.txt").read().decode("utf-8").strip()

    assert names == {
        "app.yaml",
        "model/exported.onnx",
        "config/runtime.yaml",
        "config/cameras.yaml",
        "config/rules.yaml",
        "services/yolo26-inference/image.txt",
    }
    assert runtime["device"] == "cpu"
    assert image_ref == "registry.local/visiox/yolo26-inference:0.1.0"
    assert result.manifest["model"]["path"] == "model/exported.onnx"


def test_build_edge_app_package_stores_symlink_target_as_regular_file(tmp_path):
    target = tmp_path / "target.onnx"
    target.write_bytes(b"target model")
    link = tmp_path / "exported.onnx"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = build_edge_app_package(
        EdgeAppPackageSpec(
            app_name="quality-line",
            version="v1",
            task="detect",
            model_format="onnx",
            model_path=link,
            runtime={},
            cameras=[],
            rules={},
            image_ref="registry.local/visiox/yolo26-inference:0.1.0",
            output_path=tmp_path / "package.tar.gz",
        )
    )

    with tarfile.open(result.package_path, "r:gz") as tar:
        info = tar.getmember("model/exported.onnx")
        data = tar.extractfile(info).read()

    assert info.isfile()
    assert not info.issym()
    assert data == b"target model"


def test_create_edge_app_version_creates_task_and_enqueues_command(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    model_id = seed_ready_trained_model(session_factory)
    app_id = client.post("/edge-apps", json={"name": "quality-line"}).json()["id"]

    response = client.post(
        f"/edge-apps/{app_id}/versions",
        json={
            "trained_model_id": model_id,
            "export_format": "onnx",
            "runtime": {"device": "cpu", "confidence": 0.4, "iou": 0.6},
            "cameras": [{"camera_id": "cam-1", "rtsp_url": "rtsp://example"}],
            "rules": {"classes": ["defect"]},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["package_uri"] == "pending"
    assert body["task_id"]
    assert len(stream_producer.commands) == 1
    command = stream_producer.commands[0]
    assert command.task_type == TaskType.BUILD_EDGE_APP_PACKAGE
    assert command.resource_refs == {"edge_app_version_id": body["id"]}
    assert command.payload["edge_app_version_id"] == body["id"]
    assert command.payload["trained_model_id"] == model_id

    list_response = client.get(f"/edge-apps/{app_id}/versions")
    assert list_response.json()["total"] == 1


def test_create_edge_app_version_rejects_duplicate_version(client: TestClient, session_factory):
    model_id = seed_ready_trained_model(session_factory)
    app_id = client.post("/edge-apps", json={"name": "quality-line"}).json()["id"]
    first = client.post(f"/edge-apps/{app_id}/versions", json={"trained_model_id": model_id, "version": "v1"})
    duplicate = client.post(f"/edge-apps/{app_id}/versions", json={"trained_model_id": model_id, "version": "v1"})

    assert first.status_code == 201
    assert duplicate.status_code == 409


def test_create_edge_app_version_marks_failed_when_enqueue_fails(session_factory):
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_edge_app_session] = override_session
    app.dependency_overrides[get_edge_app_stream_producer] = lambda: FailingStreamProducer()
    with TestClient(app) as client:
        model_id = seed_ready_trained_model(session_factory)
        app_id = client.post("/edge-apps", json={"name": "quality-line"}).json()["id"]
        response = client.post(f"/edge-apps/{app_id}/versions", json={"trained_model_id": model_id})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    with session_factory() as session:
        task = session.get(Task, body["task_id"])
        edge_app = session.get(EdgeApp, app_id)

    assert task.status == TaskStatus.FAILED.value
    assert task.error_code == "ENQUEUE_FAILED"
    assert edge_app.status == "failed"


def test_create_edge_app_version_enqueue_failure_keeps_ready_app_ready(session_factory):
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_edge_app_session] = override_session
    app.dependency_overrides[get_edge_app_stream_producer] = lambda: FailingStreamProducer()
    with TestClient(app) as client:
        model_id = seed_ready_trained_model(session_factory)
        app_id = client.post("/edge-apps", json={"name": "quality-line"}).json()["id"]
        with session_factory() as session:
            ready_version = EdgeAppVersion(
                edge_app_id=app_id,
                trained_model_id=model_id,
                version="ready-v1",
                package_uri="memory://packages/edge-apps/ready/package.tar.gz",
                manifest={},
                status="ready",
            )
            edge_app = session.get(EdgeApp, app_id)
            edge_app.status = "ready"
            session.add_all([edge_app, ready_version])
            session.commit()
        response = client.post(f"/edge-apps/{app_id}/versions", json={"trained_model_id": model_id, "version": "bad-v2"})

    assert response.status_code == 201
    with session_factory() as session:
        edge_app = session.get(EdgeApp, app_id)

    assert edge_app.status == "ready"


def create_version_for_worker(session_factory, storage: InMemoryObjectStorageClient, tmp_path: Path) -> tuple[str, str]:
    model_id = seed_ready_trained_model(session_factory, storage, tmp_path)
    with session_factory() as session:
        app = EdgeApp(name="worker-edge-app", status="packaging")
        session.add(app)
        session.flush()
        version = EdgeAppVersion(
            edge_app_id=app.id,
            trained_model_id=model_id,
            version="v1",
            package_uri="pending",
            manifest={},
            status="queued",
        )
        task = Task(
            task_type=TaskType.BUILD_EDGE_APP_PACKAGE.value,
            status=TaskStatus.QUEUED.value,
            progress=0,
            resource_type="edge_app_version",
            payload={},
        )
        session.add_all([version, task])
        session.flush()
        task.resource_id = version.id
        task.payload = {
            "edge_app_id": app.id,
            "edge_app_version_id": version.id,
            "trained_model_id": model_id,
            "export_format": "onnx",
            "runtime": {"image": "registry.local/visiox/yolo26-inference:0.1.0", "device": "cpu"},
            "cameras": [],
            "rules": {},
        }
        session.commit()
        return task.id, version.id


def test_worker_exports_model_packages_app_and_registers_version(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, version_id = create_version_for_worker(session_factory, storage, tmp_path)
    runner = FakeExportRunner()

    with session_factory() as session:
        result = run_edge_app_packaging(session, storage, runner, task_id, version_id, tmp_path / "work")

    assert result.status == "ready"
    assert result.package_uri == f"memory://packages/edge-apps/{version_id}/package.tar.gz"
    assert len(runner.commands) == 1
    assert runner.commands[0][0:2] == ["yolo", "export"]

    with session_factory() as session:
        version = session.get(EdgeAppVersion, version_id)
        task = session.get(Task, task_id)
        app = session.get(EdgeApp, version.edge_app_id)

    assert version.status == "ready"
    assert version.checksum
    assert version.manifest["model"]["path"] == "model/exported.onnx"
    assert task.status == TaskStatus.SUCCESS.value
    assert task.progress == 100
    assert app.status == "ready"
    assert storage.objects[("packages", f"edge-apps/{version_id}/package.tar.gz")]


def test_worker_rejects_payload_mismatch_and_marks_failed(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, version_id = create_version_for_worker(session_factory, storage, tmp_path)
    runner = FakeExportRunner()
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.payload = {**task.payload, "trained_model_id": "wrong-model"}
        session.add(task)
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="trained_model_id"):
            run_edge_app_packaging(session, storage, runner, task_id, version_id, tmp_path / "work")

    with session_factory() as session:
        task = session.get(Task, task_id)
        version = session.get(EdgeAppVersion, version_id)

    assert runner.commands == []
    assert task.status == TaskStatus.FAILED.value
    assert task.error_code == "INVALID_TASK_PAYLOAD"
    assert version.status == "failed"


@pytest.mark.parametrize(
    "payload_patch, expected_message",
    [
        ({"export_format": "bad"}, "unsupported export format"),
        ({"runtime": {"image": "registry.local/visiox/yolo26-inference:0.1.0", "device": "cpu", "imgsz": "640"}}, "runtime.imgsz"),
        ({"runtime": {"image": "registry.local/visiox/yolo26-inference:0.1.0", "device": "cpu", "half": "false"}}, "runtime.half"),
    ],
)
def test_worker_rejects_invalid_export_payload_types(session_factory, tmp_path, payload_patch, expected_message):
    storage = InMemoryObjectStorageClient()
    task_id, version_id = create_version_for_worker(session_factory, storage, tmp_path)
    runner = FakeExportRunner()
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.payload = {**task.payload, **payload_patch}
        session.add(task)
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match=expected_message):
            run_edge_app_packaging(session, storage, runner, task_id, version_id, tmp_path / "work")

    with session_factory() as session:
        task = session.get(Task, task_id)

    assert runner.commands == []
    assert task.error_code == "INVALID_TASK_PAYLOAD"


def test_worker_does_not_run_when_task_is_already_claimed(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, version_id = create_version_for_worker(session_factory, storage, tmp_path)
    runner = FakeExportRunner()
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.RUNNING.value
        session.add(task)
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="already claimed"):
            run_edge_app_packaging(session, storage, runner, task_id, version_id, tmp_path / "work")

    assert runner.commands == []


def test_worker_reclaims_stale_running_task(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, version_id = create_version_for_worker(session_factory, storage, tmp_path)
    runner = FakeExportRunner()
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.RUNNING.value
        task.started_at = datetime.now(UTC) - timedelta(hours=2)
        session.add(task)
        session.commit()

    with session_factory() as session:
        result = run_edge_app_packaging(
            session,
            storage,
            runner,
            task_id,
            version_id,
            tmp_path / "work",
            stale_after_seconds=60,
        )

    assert result.status == "ready"
    assert len(runner.commands) == 1


def test_dispatcher_routes_edge_app_package_tasks(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, version_id = create_version_for_worker(session_factory, storage, tmp_path)
    runner = FakeExportRunner()

    class UnexpectedTrainingRunner:
        def run(self, argv, work_dir):
            raise AssertionError("training runner should not be used")

    with session_factory() as session:
        result = dispatch_training_worker_task(
            session,
            storage,
            TrainingWorkerRunners(training=UnexpectedTrainingRunner(), export=runner),
            task_id=task_id,
            task_type=TaskType.BUILD_EDGE_APP_PACKAGE.value,
            resource_refs={"edge_app_version_id": version_id},
            work_dir=tmp_path / "dispatch",
        )

    assert result.status == "ready"
    assert len(runner.commands) == 1
