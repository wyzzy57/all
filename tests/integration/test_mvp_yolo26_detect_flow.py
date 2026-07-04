from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.dataset_samples import get_dataset_sample_session, get_object_storage_client
from visiox_api.routes.datasets import get_dataset_session
from visiox_api.routes.deployments import get_deployment_session, get_deployment_stream_producer
from visiox_api.routes.devices import get_agent_client, get_device_session
from visiox_api.routes.edge_apps import get_edge_app_session, get_edge_app_stream_producer
from visiox_api.routes.pipelines import get_pipeline_session
from visiox_api.routes.tasks import get_task_session
from visiox_api.routes.training_jobs import get_training_job_session, get_training_stream_producer
from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Annotation, BaseModel, Dataset, DatasetSample, Deployment, EdgeAppVersion, Task, TrainedModel
from visiox_deployment_worker.main import run_deployment_task
from visiox_storage.client import InMemoryObjectStorageClient
from visiox_training_worker.export_flow import ExportCommandResult, run_edge_app_packaging
from visiox_training_worker.main import CommandResult, run_training_job


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


class FakeTrainingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, argv: list[str], work_dir: Path) -> CommandResult:
        self.commands.append(argv)
        artifact = work_dir / "runs" / "train" / "weights" / "best.pt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"trained detect model")
        return CommandResult(
            exit_code=0,
            stdout="mAP50=0.93",
            metrics={"mAP50": 0.93, "precision": 0.9},
            artifact_path=artifact,
        )


class FakeExportRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, argv: list[str], work_dir: Path) -> ExportCommandResult:
        self.commands.append(argv)
        artifact = work_dir / "export" / "exported.onnx"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"exported onnx")
        return ExportCommandResult(exit_code=0, stdout="export ok", artifact_path=artifact)


class FakeAgentClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def health(self, endpoint_url: str) -> dict[str, Any]:
        self.calls.append(("health", endpoint_url, None))
        return {"service": "edge-agent", "status": "ok", "device": {"cpu": "test"}}

    def camera_test(self, endpoint_url: str, rtsp_url: str) -> dict[str, Any]:
        self.calls.append(("camera_test", endpoint_url, {"rtsp_url": rtsp_url}))
        return {"ok": True, "status": "reachable", "rtsp_url": rtsp_url}

    def deploy_app(self, endpoint_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("deploy", endpoint_url, payload))
        return {"success": True, "status": "deployed", "app": payload}

    def start_app(self, endpoint_url: str, app_id: str) -> dict[str, Any]:
        self.calls.append(("start", endpoint_url, {"app_id": app_id}))
        return {"success": True, "status": "running", "app": {"app_id": app_id}}

    def stop_app(self, endpoint_url: str, app_id: str) -> dict[str, Any]:
        self.calls.append(("stop", endpoint_url, {"app_id": app_id}))
        return {"success": True, "status": "stopped", "app": {"app_id": app_id}}

    def rollback_app(self, endpoint_url: str, app_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("rollback", endpoint_url, {"app_id": app_id, **payload}))
        return {
            "success": True,
            "status": "rolled_back",
            "app": {"app_id": app_id, "version": payload["version"]},
        }


def test_mvp_yolo26_detect_flow_runs_training_packaging_and_deployment(tmp_path):
    session_factory = _session_factory(tmp_path)
    storage = InMemoryObjectStorageClient()
    stream_producer = FakeStreamProducer()
    agent_client = FakeAgentClient()

    with _client(session_factory, storage, stream_producer, agent_client) as client:
        dataset = _create_detect_dataset_with_annotation(client, session_factory)
        base_model_id = _seed_ready_base_model(session_factory, storage, tmp_path)

        analysis = client.post(f"/datasets/{dataset['id']}/analyze")
        validation = client.post(f"/datasets/{dataset['id']}/validate")
        pipeline = client.post(
            "/pipelines",
            json={
                "name": "mvp-detect",
                "task": "detect",
                "scale": "n",
                "base_model_id": base_model_id,
                "dataset_id": dataset["id"],
                "params_template": {"epochs": 1, "batch": 1, "imgsz": 640},
                "default_environment": {"device": "cpu"},
            },
        )
        training_job = client.post(f"/pipelines/{pipeline.json()['id']}/jobs", json={})

        assert analysis.status_code == 201
        assert analysis.json()["payload"]["result"]["sample_count"] == 1
        assert validation.status_code == 201
        assert validation.json()["status"] == TaskStatus.SUCCESS.value
        assert pipeline.status_code == 201
        assert training_job.status_code == 201
        assert stream_producer.commands[-1].task_type == TaskType.TRAIN_MODEL

        training_runner = FakeTrainingRunner()
        with session_factory() as session:
            trained = run_training_job(
                session,
                storage,
                training_runner,
                training_job.json()["task_id"],
                training_job.json()["id"],
                tmp_path / "training-work",
            )

        assert trained.status == "success"
        assert trained.trained_model_id is not None
        assert len(training_runner.commands) == 1

        edge_app = client.post("/edge-apps", json={"name": "mvp-quality-line"})
        edge_version = client.post(
            f"/edge-apps/{edge_app.json()['id']}/versions",
            json={
                "trained_model_id": trained.trained_model_id,
                "version": "v1",
                "export_format": "onnx",
                "runtime": {"device": "cpu", "confidence": 0.3, "iou": 0.6},
                "cameras": [{"camera_id": "line-1", "rtsp_url": "rtsp://example/line-1"}],
                "rules": {"classes": ["defect"]},
            },
        )

        assert edge_app.status_code == 201
        assert edge_version.status_code == 201
        assert stream_producer.commands[-1].task_type == TaskType.BUILD_EDGE_APP_PACKAGE

        export_runner = FakeExportRunner()
        with session_factory() as session:
            packaged = run_edge_app_packaging(
                session,
                storage,
                export_runner,
                edge_version.json()["task_id"],
                edge_version.json()["id"],
                tmp_path / "packaging-work",
            )

        assert packaged.status == "ready"
        assert packaged.package_uri == f"memory://packages/edge-apps/{edge_version.json()['id']}/package.tar.gz"
        assert len(export_runner.commands) == 1

        device = client.post("/devices", json={"name": "edge-1", "endpoint_url": "http://edge-agent.local"})
        deployment = client.post(
            "/deployments",
            json={"device_id": device.json()["id"], "edge_app_version_id": edge_version.json()["id"]},
        )

        assert device.status_code == 201
        assert deployment.status_code == 201
        assert stream_producer.commands[-1].task_type == TaskType.DEPLOY_APP

        with session_factory() as session:
            deployed = run_deployment_task(
                session,
                storage,
                agent_client,
                deployment.json()["task_id"],
                deployment.json()["id"],
                tmp_path / "deployment-work",
            )

    assert deployed.status == "running"
    assert [call[0] for call in agent_client.calls] == ["deploy", "start"]
    with session_factory() as session:
        final_deployment = session.get(Deployment, deployment.json()["id"])
        final_version = session.get(EdgeAppVersion, edge_version.json()["id"])
        final_model = session.get(TrainedModel, trained.trained_model_id)
        terminal_tasks = session.scalars(select(Task).where(Task.status == TaskStatus.SUCCESS.value)).all()

    assert final_deployment is not None
    assert final_deployment.active is True
    assert final_version is not None
    assert final_version.status == "ready"
    assert final_model is not None
    assert final_model.status == "ready"
    assert len(terminal_tasks) >= 5
    assert storage.objects[("models", f"trained/{trained.trained_model_id}/best.pt")] == b"trained detect model"
    assert storage.objects[("packages", f"edge-apps/{edge_version.json()['id']}/package.tar.gz")]


def _session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-mvp.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def _client(
    session_factory,
    storage: InMemoryObjectStorageClient,
    stream_producer: FakeStreamProducer,
    agent_client: FakeAgentClient,
) -> Generator[TestClient]:
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    for dependency in [
        get_dataset_session,
        get_dataset_sample_session,
        get_task_session,
        get_pipeline_session,
        get_training_job_session,
        get_edge_app_session,
        get_device_session,
        get_deployment_session,
    ]:
        app.dependency_overrides[dependency] = override_session
    app.dependency_overrides[get_object_storage_client] = lambda: storage
    app.dependency_overrides[get_training_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_edge_app_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_deployment_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_agent_client] = lambda: agent_client
    app.dependency_overrides[get_settings] = lambda: Settings()

    with TestClient(app) as test_client:
        yield test_client


def _create_detect_dataset_with_annotation(client: TestClient, session_factory) -> dict[str, Any]:
    dataset = client.post(
        "/datasets",
        json={"name": "mvp-detect-dataset", "task": "detect", "class_schema": {"names": ["ok", "defect"]}},
    ).json()
    upload = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("part.png", _image_bytes(), "image/png")},
    )
    assert upload.status_code == 201
    sample_id = upload.json()["samples"][0]["id"]

    split = client.post(
        f"/datasets/{dataset['id']}/samples/splits",
        json={"assignments": [{"sample_id": sample_id, "split": "train"}]},
    )
    assert split.status_code == 200

    with session_factory() as session:
        sample = session.get(DatasetSample, sample_id)
        dataset_row = session.get(Dataset, dataset["id"])
        sample.annotation_status = "labeled"
        dataset_row.annotation_count = 1
        dataset_row.status = "validated"
        session.add(
            Annotation(
                dataset_sample_id=sample.id,
                source="mvp-fixture",
                internal_payload={
                    "annotations": [
                        {
                            "source_annotation_id": "mvp-annotation-1",
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
        session.add_all([sample, dataset_row])
        session.commit()

    return dataset


def _seed_ready_base_model(
    session_factory,
    storage: InMemoryObjectStorageClient,
    tmp_path: Path,
) -> str:
    model_object = "base/yolo26n-detect.pt"
    model_path = tmp_path / "yolo26n-detect.pt"
    model_path.write_bytes(b"base detect model")
    storage.put_file("models", model_object, model_path)
    with session_factory() as session:
        model = BaseModel(
            family="yolo26",
            task="detect",
            scale="n",
            filename="yolo26n-detect.pt",
            source_path="file:///models/yolo26n-detect.pt",
            local_uri=f"memory://models/{model_object}",
            status="ready",
        )
        session.add(model)
        session.commit()
        return model.id


def _image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 48), color=(40, 80, 120)).save(buffer, format="PNG")
    return buffer.getvalue()
