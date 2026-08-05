from __future__ import annotations

import importlib
import json


def _telemetry_module():
    return importlib.import_module("visiox_paddlex_training_worker.telemetry")


class FakeMlflow:
    def __init__(self, *, fail_metrics: bool = False) -> None:
        self.fail_metrics = fail_metrics
        self.started: list[dict[str, object]] = []
        self.metrics: list[tuple[dict[str, float], int]] = []
        self.ended: list[str] = []

    def start_run(self, **kwargs) -> None:
        self.started.append(kwargs)

    def log_metrics(self, metrics, *, step: int) -> None:
        if self.fail_metrics:
            raise ConnectionError("offline")
        self.metrics.append((metrics, step))

    def end_run(self, *, status: str) -> None:
        self.ended.append(status)


class FakeWriter:
    def __init__(self, *, fail_write: bool = False) -> None:
        self.scalars: list[tuple[str, float, int]] = []
        self.closed = False
        self.fail_write = fail_write

    def add_scalar(self, name: str, value: float, step: int) -> None:
        if self.fail_write:
            raise OSError("tensorboard disk full")
        self.scalars.append((name, value, step))

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_parser_normalizes_training_and_evaluation_log_lines() -> None:
    parse = _telemetry_module().parse_log_line

    training = parse(
        "Epoch: [3] [  40/100] learning_rate: 0.00025 loss: 1.75 "
        "loss_cls: 0.50 loss_bbox: 1.25"
    )
    evaluation = parse(
        "bbox_mAP: 0.412 bbox_mAP_50: 0.681 bbox_mAP_75: 0.433 "
        "bbox_mAP_s: 0.201 bbox_mAP_m: 0.455 bbox_mAP_l: 0.612 AR: 0.537"
    )

    assert training == {
        "step": 340,
        "epoch_step": 40,
        "epoch": 3.0,
        "total_steps": 100,
        "learning_rate": 0.00025,
        "loss": 1.75,
        "loss_cls": 0.5,
        "loss_bbox": 1.25,
    }
    assert evaluation == {
        "bbox_mAP": 0.412,
        "bbox_mAP_50": 0.681,
        "bbox_mAP_75": 0.433,
        "bbox_mAP_s": 0.201,
        "bbox_mAP_m": 0.455,
        "bbox_mAP_l": 0.612,
        "AR": 0.537,
    }


def test_parser_normalizes_real_coco_ap_and_ar_summary_lines() -> None:
    parse = _telemetry_module().parse_log_line

    assert parse(
        " Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | "
        "maxDets=100 ] = 0.412"
    ) == {"bbox_mAP": 0.412}
    assert parse(
        " Average Precision  (AP) @[ IoU=0.50      | area=   all | "
        "maxDets=100 ] = 0.681"
    ) == {"bbox_mAP_50": 0.681}
    assert parse(
        " Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | "
        "maxDets=100 ] = 0.201"
    ) == {"bbox_mAP_s": 0.201}
    assert parse(
        " Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | "
        "maxDets=100 ] = 0.537"
    ) == {"AR": 0.537}
    assert parse(
        " Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | "
        "maxDets=100 ] = 0.721"
    ) == {"AR_l": 0.721}


def test_recorder_writes_canonical_jsonl_and_mirrors_metrics(tmp_path) -> None:
    module = _telemetry_module()
    mlflow = FakeMlflow()
    writer = FakeWriter()
    recorder = module.TelemetryRecorder(
        tmp_path,
        run_name="visiox-job-1",
        tags={"visiox.training_job_id": "job-1"},
        mlflow_module=mlflow,
        writer_factory=lambda _: writer,
    )

    assert recorder.record_line(
        "Epoch: [1] [2/8] learning_rate: 0.001 loss: 2.5"
    ) is True
    assert recorder.record_line(
        "Epoch: [1] [2/8] learning_rate: 0.001 loss: 2.5"
    ) is False
    recorder.close(status="FINISHED")

    rows = [
        json.loads(line)
        for line in recorder.metrics_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["source"] == "paddlex_log"
    assert rows[0]["step"] == 10
    assert rows[0]["epoch"] == 1.0
    assert rows[0]["metrics"] == {"learning_rate": 0.001, "loss": 2.5}
    assert mlflow.started == [
        {
            "run_name": "visiox-job-1",
            "tags": {"visiox.training_job_id": "job-1"},
        }
    ]
    assert mlflow.metrics == [({"learning_rate": 0.001, "loss": 2.5}, 10)]
    assert writer.scalars == [
        ("learning_rate", 0.001, 10),
        ("loss", 2.5, 10),
    ]
    assert writer.closed is True


def test_recorder_uses_monotonic_steps_across_epochs(tmp_path) -> None:
    module = _telemetry_module()
    writer = FakeWriter()
    recorder = module.TelemetryRecorder(
        tmp_path,
        run_name="visiox-job-1",
        tags={},
        mlflow_module=None,
        writer_factory=lambda _: writer,
    )

    assert recorder.record_line("Epoch: [0] [99/100] loss: 2.0") is True
    assert recorder.record_line("Epoch: [1] [ 0/100] loss: 1.5") is True

    assert recorder.step == 100
    assert [item[2] for item in writer.scalars] == [99, 100]


def test_mlflow_failure_keeps_jsonl_and_tensorboard_available(tmp_path) -> None:
    module = _telemetry_module()
    writer = FakeWriter()
    mlflow = FakeMlflow(fail_metrics=True)
    recorder = module.TelemetryRecorder(
        tmp_path,
        run_name="visiox-job-1",
        tags={},
        mlflow_module=mlflow,
        writer_factory=lambda _: writer,
    )

    assert recorder.record_line("Epoch: [1] [1/2] loss: 3.0") is True
    recorder.close(status="FAILED")

    assert recorder.mlflow_available is False
    assert recorder.mlflow_reason == "offline"
    assert mlflow.ended == ["FAILED"]
    assert json.loads(recorder.metrics_path.read_text(encoding="utf-8"))["metrics"] == {
        "loss": 3.0
    }
    assert writer.scalars == [("loss", 3.0, 3)]


def test_real_coco_metrics_generate_evaluation_report(tmp_path) -> None:
    module = _telemetry_module()
    recorder = module.TelemetryRecorder(
        tmp_path,
        run_name="visiox-job-1",
        tags={},
        mlflow_module=None,
        writer_factory=lambda _: None,
    )

    recorder.record_line(
        " Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | "
        "maxDets=100 ] = 0.412"
    )
    recorder.record_line(
        " Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | "
        "maxDets=100 ] = 0.537"
    )
    recorder.close(status="FINISHED")

    report = json.loads(
        (tmp_path / "evaluation" / "evaluation.json").read_text(encoding="utf-8")
    )
    assert report == {
        "schema_version": "1.0",
        "framework": "paddlex",
        "metrics": {"AR": 0.537, "bbox_mAP": 0.412},
    }


def test_tensorboard_write_failure_closes_writer(tmp_path) -> None:
    module = _telemetry_module()
    writer = FakeWriter(fail_write=True)
    recorder = module.TelemetryRecorder(
        tmp_path,
        run_name="visiox-job-1",
        tags={},
        mlflow_module=None,
        writer_factory=lambda _: writer,
    )

    assert recorder.record_line("Epoch: [0] [1/2] loss: 3.0") is True

    assert recorder.tensorboard_available is False
    assert recorder.tensorboard_reason == "tensorboard disk full"
    assert writer.closed is True
