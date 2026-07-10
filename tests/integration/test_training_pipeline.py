from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.pipelines import get_pipeline_session
from visiox_api.routes.tasks import get_task_session
from visiox_api.routes.training_jobs import (
    get_training_job_session,
    get_training_object_storage_client,
    get_training_stream_producer,
)
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import (
    Annotation,
    BaseModel,
    Dataset,
    DatasetSample,
    Task,
    TrainedModel,
    TrainingJob,
    TrainingPipeline,
)
from visiox_storage.client import InMemoryObjectStorageClient
import visiox_training_worker.main as training_worker_main
from visiox_training_worker.main import CommandResult, run_training_job
from visiox_training_worker.runner import SubprocessTrainingRunner, run_pending_training_tasks


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


class FailingStreamProducer:
    async def enqueue(self, command):
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
    def put_file(self, bucket: str, object_name: str, file_path: Path, content_type: str | None = None) -> str:
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

    app.dependency_overrides[get_pipeline_session] = override_session
    app.dependency_overrides[get_task_session] = override_session
    app.dependency_overrides[get_training_job_session] = override_session
    app.dependency_overrides[get_training_stream_producer] = lambda: stream_producer

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
        )
        session.add_all([base_model, dataset])
        session.flush()
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


def create_pipeline(client: TestClient, base_model_id: str, dataset_id: str, **overrides):
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


def test_create_pipeline_validates_base_model_dataset_and_lists_filters(client: TestClient, session_factory):
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


def test_update_and_delete_pipeline_visibility_settings(client: TestClient, session_factory):
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
    assert updated.json()["public_scope"] == {"departments": ["管理员部门"], "groups": ["1组"]}
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_update_pipeline_revalidates_and_syncs_base_model_task_scale(client: TestClient, session_factory):
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory, scale="l")
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

    updated = client.patch(f"/pipelines/{pipeline_id}", json={"base_model_id": next_base_model_id})

    assert updated.status_code == 200
    body = updated.json()
    assert body["base_model_id"] == next_base_model_id
    assert body["task"] == "detect"
    assert body["scale"] == "n"


def test_create_pipeline_accepts_ultralytics_yolo26_training_params(client: TestClient, session_factory):
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


def test_create_pipeline_maps_legacy_warmup_steps_to_ultralytics_warmup_epochs(client: TestClient, session_factory):
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
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory, **seed_kwargs)

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

    list_response = client.get(f"/training-jobs?pipeline_id={pipeline_id}&status=queued")
    detail_response = client.get(f"/training-jobs/{body['id']}")
    assert list_response.json()["total"] == 1
    assert detail_response.json()["task_id"] == body["task_id"]
    with session_factory() as session:
        assert session.get(TrainingPipeline, pipeline_id).status == "running"


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


def test_create_training_job_revalidates_pipeline_resources(client: TestClient, session_factory):
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


def test_create_training_job_marks_job_and_task_failed_when_enqueue_fails(session_factory):
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_session] = override_session
    app.dependency_overrides[get_training_job_session] = override_session
    app.dependency_overrides[get_training_stream_producer] = lambda: FailingStreamProducer()
    with TestClient(app) as client:
        base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory)
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


def _create_job_for_worker(session_factory, tmp_path, storage: InMemoryObjectStorageClient) -> tuple[str, str]:
    base_model_id, dataset_id, _sample_id = seed_training_ready_rows(session_factory, storage, tmp_path)
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


def test_worker_runs_training_exports_dataset_and_registers_model(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    runner = FakeRunner()

    with session_factory() as session:
        result = run_training_job(session, storage, runner, task_id, job_id, tmp_path / "work")

    assert result.training_job_id == job_id
    assert result.trained_model_id
    assert len(runner.commands) == 1
    assert runner.commands[0][0] == "yolo"
    assert "train" in runner.commands[0]
    assert "optimizer=MuSGD" in runner.commands[0]
    assert "cos_lr=True" in runner.commands[0]
    assert "classes=[0,1]" in runner.commands[0]
    assert "device=cpu" in runner.commands[0]
    assert not isinstance(runner.commands[0], str)
    data_arg = next(argument for argument in runner.commands[0] if argument.startswith("data="))
    data_yaml_path = Path(data_arg.split("=", 1)[1])
    assert f"path: {data_yaml_path.parent.resolve().as_posix()}" in data_yaml_path.read_text(encoding="utf-8")

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        trained_model = session.get(TrainedModel, result.trained_model_id)
        pipeline = session.get(TrainingPipeline, job.pipeline_id)
        trained_models = session.scalars(select(TrainedModel).where(TrainedModel.pipeline_id == pipeline.id)).all()

    assert job.status == "success"
    assert pipeline.status == "success"
    assert job.trained_model_id == trained_model.id
    assert trained_model.name == "best.pt"
    assert job.metrics["mAP50"] == 0.91
    assert job.metrics["precision"] == 0.88
    assert set(job.metrics["weights"]) == {"best.pt", "last.pt"}
    assert job.log_uri.startswith("memory://training/")
    assert task.status == TaskStatus.SUCCESS.value
    assert task.progress == 100
    assert {model.name for model in trained_models} == {"best.pt", "last.pt"}
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
    result_uri = storage.put_file("training", "jobs/job-1/visualizations/results.png", result_path)

    with session_factory() as session:
        pipeline = TrainingPipeline(name="artifact-pipeline", task="detect", scale="n", status="success")
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


def test_training_job_artifacts_use_stored_filename_for_legacy_model_rows(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    weight_path = tmp_path / "best.pt"
    weight_path.write_bytes(b"legacy best model")
    weight_uri = storage.put_file("models", "trained/model-legacy/best.pt", weight_path)

    with session_factory() as session:
        pipeline = TrainingPipeline(name="legacy-artifact-pipeline", task="detect", scale="n", status="success")
        session.add(pipeline)
        session.flush()
        job = TrainingJob(id="job-legacy", pipeline_id=pipeline.id, status="success", metrics={})
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
    assert result.artifact_path == tmp_path / "runs" / "job-test" / "weights" / "best.pt"
    assert result.weight_paths == {
        "best.pt": tmp_path / "runs" / "job-test" / "weights" / "best.pt",
        "last.pt": tmp_path / "runs" / "job-test" / "weights" / "last.pt",
    }
    assert result.metrics == {"epoch": 1, "metrics/mAP50(B)": 0.91, "metrics/precision(B)": 0.88}
    assert result.visualization_paths == {"results.png": tmp_path / "runs" / "job-test" / "results.png"}


def test_worker_falls_back_to_cpu_when_cuda_device_requested_without_cuda(session_factory, tmp_path, monkeypatch):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    monkeypatch.setattr(training_worker_main, "_cuda_is_available", lambda: False)
    runner = FakeRunner()

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        job.params = {**job.params, "device": "0"}
        task.payload = {**task.payload, "params": job.params, "environment": {"device": "0"}}
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
            run_training_job(session, storage, FailingRunner(), task_id, job_id, tmp_path / "work")

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
    assert not any(bucket == "models" and object_name.startswith("trained/") for bucket, object_name in storage.objects)


def test_worker_marks_pipeline_canceled_when_training_is_canceled(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="training canceled"):
            run_training_job(session, storage, CancelingRunner(session_factory, task_id), task_id, job_id, tmp_path / "work")

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        pipeline = session.get(TrainingPipeline, job.pipeline_id)

    assert job.status == "canceled"
    assert pipeline.status == "canceled"
    assert task.status == TaskStatus.CANCELED.value
    assert task.error_code == "TRAINING_CANCELED"
    assert task.retryable is False


def test_run_pending_training_tasks_keeps_worker_alive_when_task_fails(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, _job_id = _create_job_for_worker(session_factory, tmp_path, storage)

    with session_factory() as session:
        processed = run_pending_training_tasks(session, storage, FailingRunner(), tmp_path / "work")

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
            run_training_job(session, storage, runner, task_id, job_id, tmp_path / "work")

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
            run_training_job(session, storage, runner, task_id, job_id, tmp_path / "work")

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
        job = TrainingJob(pipeline_id=pipeline.id, status="queued", params={"epochs": 2, "batch": 4, "imgsz": 640})
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
        run_training_job(session, storage, FakeRunner(), task_id, job_id, tmp_path / "work")

    assert (tmp_path / "work" / "base-model" / "base.pt").exists()
    assert not (tmp_path / "work" / "escape.pt").exists()


def test_worker_replaces_placeholder_base_model_with_real_ultralytics_asset(session_factory, tmp_path, monkeypatch):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)
    storage.objects[("models", "base/detect-n.pt")] = b"visiox prepared yolo26 base model placeholder: yolo26-detect-n\n"
    downloaded = tmp_path / "downloaded.pt"
    downloaded.write_bytes(b"real ultralytics weights")

    def fake_download(filename: str, target_dir: Path) -> Path:
        assert filename.endswith(".pt")
        assert target_dir.name == "base-model"
        return downloaded

    monkeypatch.setattr(training_worker_main, "_download_base_model_asset", fake_download)
    runner = InspectingRunner()

    with session_factory() as session:
        run_training_job(session, storage, runner, task_id, job_id, tmp_path / "work")

    assert runner.base_model_bytes == b"real ultralytics weights"
    assert storage.objects[("models", "base/detect-n.pt")] == b"real ultralytics weights"


def test_worker_cleans_uploaded_artifacts_when_later_persist_fails(session_factory, tmp_path):
    storage = FailingLogStorage()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="log upload failed"):
            run_training_job(session, storage, FakeRunner(), task_id, job_id, tmp_path / "work")

    assert not any(bucket == "models" and object_name.startswith("trained/") for bucket, object_name in storage.objects)
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
        first = run_training_job(session, storage, runner, task_id, job_id, tmp_path / "work")
    with session_factory() as session:
        second = run_training_job(session, storage, runner, task_id, job_id, tmp_path / "work-again")

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
