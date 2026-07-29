from __future__ import annotations

import json

from visiox_llm_training_worker.config import RunIdentity
from visiox_llm_training_worker.telemetry import TelemetryRecorder


class FakeMlflow:
    def __init__(self, *, fail_metrics: bool = False) -> None:
        self.fail_metrics = fail_metrics
        self.started = []
        self.metrics = []
        self.ended = []
        self.tracking_uris = []

    def set_tracking_uri(self, uri: str) -> None:
        self.tracking_uris.append(uri)

    def start_run(self, **kwargs) -> None:
        self.started.append(kwargs)

    def log_metrics(self, metrics, *, step: int) -> None:
        if self.fail_metrics:
            raise ConnectionError("offline")
        self.metrics.append((metrics, step))

    def end_run(self, *, status: str) -> None:
        self.ended.append(status)


class FakeWriter:
    def __init__(self) -> None:
        self.scalars = []
        self.closed = False

    def add_scalar(self, name, value, step) -> None:
        self.scalars.append((name, value, step))

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def identity() -> RunIdentity:
    return RunIdentity(
        training_job_id="job-1",
        distributed_run_id="run-1",
        attempt=1,
        organization_id="org-1",
        owner_user_id="user-1",
        pipeline_id="pipeline-1",
        node_ids=("node-a",),
        model_revision="revision",
        dataset_version_id="version-1",
        dataset_checksum="d" * 64,
        training_image_digest="registry/llm@sha256:" + "e" * 64,
        mlflow_tracking_uri="http://mlflow.test:5000",
    )


def test_telemetry_records_normalized_metrics_once_per_payload(tmp_path) -> None:
    mlflow = FakeMlflow()
    writer = FakeWriter()
    recorder = TelemetryRecorder(
        tmp_path,
        identity(),
        mlflow_module=mlflow,
        writer_factory=lambda _: writer,
    )
    payload = {
        "current_steps": 3,
        "epoch": 1.0,
        "loss": 0.8,
        "eval_loss": 0.9,
        "lr": 0.0001,
        "grad_norm": 1.2,
        "train_samples_per_second": 8.5,
    }

    assert recorder.record(payload) is True
    assert recorder.record(payload) is False
    recorder.close(status="FINISHED")

    rows = [json.loads(line) for line in recorder.metrics_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["step"] == 3
    assert rows[0]["learning_rate"] == 0.0001
    assert rows[0]["samples_per_second"] == 8.5
    assert mlflow.started[0]["run_name"] == "visiox-job-1-attempt-1"
    assert mlflow.tracking_uris == ["http://mlflow.test:5000"]
    assert mlflow.metrics[0][1] == 3
    assert writer.closed is True


def test_mlflow_outage_falls_back_to_jsonl_without_raising(tmp_path) -> None:
    recorder = TelemetryRecorder(
        tmp_path,
        identity(),
        mlflow_module=FakeMlflow(fail_metrics=True),
        writer_factory=lambda _: FakeWriter(),
    )

    assert recorder.record({"current_steps": 1, "loss": 1.5}) is True
    assert recorder.mlflow_available is False
    assert recorder.mlflow_reason == "offline"
    assert json.loads(recorder.metrics_path.read_text(encoding="utf-8"))["loss"] == 1.5
