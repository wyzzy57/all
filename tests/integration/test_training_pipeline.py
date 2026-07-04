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
from visiox_api.routes.training_jobs import get_training_job_session, get_training_stream_producer
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
from visiox_training_worker.main import CommandResult, run_training_job


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

    def run(self, argv: list[str], work_dir: Path) -> CommandResult:
        self.commands.append(argv)
        artifact = work_dir / "runs" / "train" / "weights" / "best.pt"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"trained model")
        return CommandResult(
            exit_code=0,
            stdout="mAP50=0.91",
            stderr="",
            metrics={"mAP50": 0.91, "precision": 0.88},
            artifact_path=artifact,
        )


class FailingRunner:
    def run(self, argv: list[str], work_dir: Path) -> CommandResult:
        del argv, work_dir
        raise RuntimeError("cuda out of memory")


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


@pytest.mark.parametrize(
    ("seed_kwargs", "payload_overrides", "expected_status"),
    [
        ({"base_status": "remote_available"}, {}, 409),
        ({"dataset_status": "created"}, {}, 409),
        ({}, {"scale": "s"}, 409),
        ({"dataset_task": "segment"}, {}, 409),
        ({"sample_count": 0, "annotation_count": 0}, {}, 409),
        ({"annotation_count": 0}, {}, 409),
        ({}, {"params_template": {"epochs": -1}}, 422),
        ({}, {"params_template": {"epochs": 1, "unknown": 2}}, 422),
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

    assert job.status == "failed"
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
            params={"epochs": 2, "batch": 4, "imgsz": 640, "device": "cpu"},
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
    assert not isinstance(runner.commands[0], str)

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        trained_model = session.get(TrainedModel, result.trained_model_id)

    assert job.status == "success"
    assert job.trained_model_id == trained_model.id
    assert job.metrics == {"mAP50": 0.91, "precision": 0.88}
    assert job.log_uri.startswith("memory://training/")
    assert task.status == TaskStatus.SUCCESS.value
    assert task.progress == 100
    assert trained_model.artifact_uri.startswith("memory://models/trained/")
    assert storage.objects[("models", f"trained/{trained_model.id}/best.pt")] == b"trained model"


def test_worker_marks_task_and_job_failed_when_runner_fails(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, job_id = _create_job_for_worker(session_factory, tmp_path, storage)

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="cuda out of memory"):
            run_training_job(session, storage, FailingRunner(), task_id, job_id, tmp_path / "work")

    with session_factory() as session:
        job = session.get(TrainingJob, job_id)
        task = session.get(Task, task_id)
        trained_models = session.scalars(select(TrainedModel)).all()

    assert job.status == "failed"
    assert task.status == TaskStatus.FAILED.value
    assert task.stage == "train"
    assert task.error_code == "TRAINING_FAILED"
    assert "cuda out of memory" in task.error_message
    assert task.retryable is True
    assert trained_models == []
    assert not any(bucket == "models" and object_name.startswith("trained/") for bucket, object_name in storage.objects)


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
