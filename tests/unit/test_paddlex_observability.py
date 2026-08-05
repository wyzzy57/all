from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from visiox_api.services.observability.paddlex import PaddleXObservabilityAdapter
from visiox_api.services.observability.paddlex import parse_log_line
from visiox_api.services.observability.ultralytics import (
    UltralyticsObservabilityAdapter,
)
from visiox_api.services.training_observability import TrainingObservabilityService


class OfflineMlflow:
    def __init__(self, _uri: str) -> None:
        raise ConnectionError("mlflow offline")


class PaddleMlflow:
    def search_experiments(self):
        return [SimpleNamespace(experiment_id="1", lifecycle_stage="active")]

    def search_runs(self, *, experiment_ids, filter_string):
        del experiment_ids, filter_string
        return [
            SimpleNamespace(
                info=SimpleNamespace(run_id="run-1"),
                data=SimpleNamespace(metrics={"loss": 2.0}),
            )
        ]

    def get_metric_history(self, run_id, key):
        assert run_id == "run-1"
        return (
            [SimpleNamespace(step=2, value=2.0, timestamp=2_000)]
            if key == "loss"
            else []
        )


class PaddleEvents:
    def Reload(self):
        return self

    def Tags(self):
        return {"scalars": ["bbox_mAP_50"], "histograms": []}

    def Scalars(self, tag):
        assert tag == "bbox_mAP_50"
        return [SimpleNamespace(step=2, value=0.66, wall_time=2.0)]


class FakeVisualDLReader:
    def get_tags(self):
        return {
            "scalar": ["loss", "learning_rate", "bbox_mAP_50"],
        }

    def get_data(self, component, tag):
        assert component == "scalar"
        return {
            "loss": [
                SimpleNamespace(id=1, tag="loss", timestamp=10.0, value=2.5)
            ],
            "learning_rate": [
                SimpleNamespace(
                    id=1,
                    tag="learning_rate",
                    timestamp=10.0,
                    value=0.001,
                )
            ],
            "bbox_mAP_50": [
                SimpleNamespace(
                    id=2,
                    tag="bbox_mAP_50",
                    timestamp=20.0,
                    value=0.68,
                )
            ],
        }[tag]


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        mlflow_tracking_uri="http://mlflow.test",
        mlflow_public_url="http://mlflow.test",
        tensorboard_public_url="http://tensorboard.test",
        visualdl_public_url="http://visualdl.test",
        training_runs_root=tmp_path,
    )


def _job() -> SimpleNamespace:
    return SimpleNamespace(id="paddlex-1", status="running", metrics={})


def test_for_engine_registers_paddlex_adapter(tmp_path: Path) -> None:
    service = TrainingObservabilityService(_settings(tmp_path))

    assert isinstance(service.for_engine("paddlex"), PaddleXObservabilityAdapter)


def test_detection_metric_canonical_names_match_across_adapters(
    tmp_path: Path,
) -> None:
    service = TrainingObservabilityService(_settings(tmp_path))
    ultralytics = UltralyticsObservabilityAdapter(service)
    paddlex = PaddleXObservabilityAdapter(service)
    equivalent_metrics = [
        ("metrics/mAP50(B)", "bbox_mAP_50"),
        ("metrics/mAP50-95(B)", "bbox_mAP"),
        ("metrics/mAP75(B)", "bbox_mAP_75"),
        ("metrics/mAPs(B)", "bbox_mAP_s"),
        ("metrics/mAPm(B)", "bbox_mAP_m"),
        ("metrics/mAPl(B)", "bbox_mAP_l"),
        ("metrics/AR(B)", "AR"),
    ]

    for ultralytics_name, paddlex_name in equivalent_metrics:
        ultralytics_identity = ultralytics.metric_identity(ultralytics_name)
        paddlex_identity = paddlex.metric_identity(paddlex_name)
        assert ultralytics_identity.canonical_name == paddlex_identity.canonical_name
        assert ultralytics_identity.raw_name == ultralytics_name
        assert paddlex_identity.raw_name == paddlex_name


def test_paddlex_reads_visualdl_scalars_with_canonical_metadata(
    tmp_path: Path,
) -> None:
    job = _job()
    visualdl_dir = tmp_path / "runs" / f"job-{job.id}" / "visualdl"
    visualdl_dir.mkdir(parents=True)
    record_path = visualdl_dir / "vdlrecords.1.log"
    record_path.touch()
    opened_paths: list[str] = []

    def reader_factory(file_path: str):
        opened_paths.append(file_path)
        return FakeVisualDLReader()

    service = TrainingObservabilityService(
        _settings(tmp_path),
        mlflow_client_factory=OfflineMlflow,
        visualdl_reader_factory=reader_factory,
    )

    result = service.for_engine("paddlex").scalars(
        job,
        ["loss.total", "optimization.learning_rate", "detection.ap50"],
        None,
        None,
        100,
    )

    assert opened_paths == [str(record_path)]
    assert result["series"]["loss.total"][0] == {
        "canonical_name": "loss.total",
        "raw_name": "loss",
        "unit": "loss",
        "split": "train",
        "step": 1.0,
        "epoch": None,
        "value": 2.5,
        "timestamp": 10.0,
        "source": "visualdl",
    }
    assert result["series"]["optimization.learning_rate"][0]["source"] == (
        "visualdl"
    )
    assert result["series"]["detection.ap50"][0]["raw_name"] == "bbox_mAP_50"
    assert result["availability"]["visualdl"] == {
        "available": True,
        "reason": None,
    }


def test_paddlex_visualdl_failure_falls_back_to_jsonl(tmp_path: Path) -> None:
    job = _job()
    run_path = tmp_path / "runs" / f"job-{job.id}"
    visualdl_dir = run_path / "visualdl"
    visualdl_dir.mkdir(parents=True)
    (visualdl_dir / "vdlrecords.corrupt.log").touch()
    (run_path / "visiox-metrics.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 12.0,
                "step": 3,
                "metrics": {"loss": 1.25},
            }
        ),
        encoding="utf-8",
    )

    def broken_reader(_file_path: str):
        raise ValueError("corrupt vdlrecords")

    service = TrainingObservabilityService(
        _settings(tmp_path),
        mlflow_client_factory=OfflineMlflow,
        visualdl_reader_factory=broken_reader,
    )

    result = service.for_engine("paddlex").scalars(
        job, ["loss.total"], None, None, 100
    )

    assert result["series"]["loss.total"][0]["source"] == "jsonl"
    assert result["availability"]["visualdl"] == {
        "available": False,
        "reason": "corrupt vdlrecords",
    }


def test_paddlex_visualdl_get_data_failure_falls_back_to_jsonl(
    tmp_path: Path,
) -> None:
    job = _job()
    run_path = tmp_path / "runs" / f"job-{job.id}"
    visualdl_dir = run_path / "visualdl"
    visualdl_dir.mkdir(parents=True)
    (visualdl_dir / "vdlrecords.corrupt-data.log").touch()
    (run_path / "visiox-metrics.jsonl").write_text(
        json.dumps(
            {"timestamp": 12.0, "step": 3, "metrics": {"loss": 1.25}}
        ),
        encoding="utf-8",
    )

    class BrokenDataReader:
        def get_tags(self):
            return {"scalar": ["loss"]}

        def get_data(self, component, tag):
            assert component == "scalar"
            assert tag == "loss"
            raise ValueError("VisualDL scalar payload is corrupt")

    service = TrainingObservabilityService(
        _settings(tmp_path),
        mlflow_client_factory=OfflineMlflow,
        visualdl_reader_factory=lambda _path: BrokenDataReader(),
    )

    result = service.for_engine("paddlex").scalars(
        job, ["loss.total"], None, None, 100
    )

    assert result["series"]["loss.total"][0]["source"] == "jsonl"
    assert result["availability"]["visualdl"] == {
        "available": False,
        "reason": "VisualDL scalar payload is corrupt",
    }


def test_paddlex_jsonl_fallback_normalizes_losses_lr_and_coco_metrics(
    tmp_path: Path,
) -> None:
    job = _job()
    run_path = tmp_path / "runs" / f"job-{job.id}"
    run_path.mkdir(parents=True)
    rows = [
        {
            "schema_version": "1.0",
            "source": "paddlex_log",
            "timestamp": 10.0,
            "step": 4,
            "epoch": 1.0,
            "metrics": {
                "loss": 2.5,
                "loss_cls": 0.5,
                "loss_bbox": 2.0,
                "learning_rate": 0.001,
            },
        },
        {
            "schema_version": "1.0",
            "source": "paddlex_log",
            "timestamp": 20.0,
            "step": 8,
            "epoch": 2.0,
            "metrics": {
                "bbox_mAP": 0.41,
                "bbox_mAP_50": 0.68,
                "bbox_mAP_75": 0.43,
                "bbox_mAP_s": 0.20,
                "bbox_mAP_m": 0.45,
                "bbox_mAP_l": 0.61,
                "AR": 0.54,
            },
        },
    ]
    (run_path / "visiox-metrics.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )
    service = TrainingObservabilityService(
        _settings(tmp_path), mlflow_client_factory=OfflineMlflow
    )

    result = service.for_engine("paddlex").scalars(
        job,
        [
            "loss.total",
            "loss.classification",
            "loss.bbox",
            "optimization.learning_rate",
            "detection.ap",
            "detection.ap50",
            "detection.ap75",
            "detection.aps",
            "detection.apm",
            "detection.apl",
            "detection.ar",
        ],
        None,
        None,
        100,
    )
    summary = service.for_engine("paddlex").summary(
        job,
        SimpleNamespace(
            id="pipeline-1", name="pipeline", status="running", engine="paddlex"
        ),
        SimpleNamespace(id="task-1", status="RUNNING"),
    )

    assert set(result["series"]) == {
        "loss.total",
        "loss.classification",
        "loss.bbox",
        "optimization.learning_rate",
        "detection.ap",
        "detection.ap50",
        "detection.ap75",
        "detection.aps",
        "detection.apm",
        "detection.apl",
        "detection.ar",
    }
    assert result["series"]["loss.total"][0] == {
        "canonical_name": "loss.total",
        "raw_name": "loss",
        "unit": "loss",
        "split": "train",
        "step": 4.0,
        "epoch": 1.0,
        "value": 2.5,
        "timestamp": 10.0,
        "source": "jsonl",
    }
    assert result["series"]["loss.classification"][0]["canonical_name"] != (
        result["series"]["loss.bbox"][0]["canonical_name"]
    )
    assert result["series"]["detection.ap50"][0]["raw_name"] == "bbox_mAP_50"
    assert result["series"]["detection.ap50"][0]["unit"] == "ratio"
    assert result["availability"]["mlflow"] == {
        "available": False,
        "reason": "mlflow offline",
    }
    assert summary["latest_metrics"]["bbox_mAP_50"] == 0.68
    assert {
        "loss.total",
        "loss.classification",
        "loss.bbox",
        "optimization.learning_rate",
        "detection.ap50",
    } <= set(summary["available_scalar_keys"])


def test_paddlex_metric_identity_only_combines_matching_family_and_unit(
    tmp_path: Path,
) -> None:
    adapter = TrainingObservabilityService(_settings(tmp_path)).for_engine("paddlex")

    train_total = adapter.metric_identity("train/loss")
    validation_total = adapter.metric_identity("val/loss")
    classification = adapter.metric_identity("loss_cls")

    assert (train_total.canonical_name, train_total.unit) == (
        validation_total.canonical_name,
        validation_total.unit,
    )
    assert train_total.split == "train"
    assert validation_total.split == "val"
    assert classification.canonical_name != train_total.canonical_name


def test_paddlex_maps_mlflow_and_tensorboard_mirror_raw_names(tmp_path: Path) -> None:
    job = _job()
    run_path = tmp_path / "runs" / f"job-{job.id}"
    run_path.mkdir(parents=True)
    (run_path / "events.out.tfevents.1").touch()
    service = TrainingObservabilityService(
        _settings(tmp_path),
        mlflow_client_factory=lambda _: PaddleMlflow(),
        event_accumulator_factory=lambda _: PaddleEvents(),
    )

    result = service.for_engine("paddlex").scalars(
        job, ["loss.total", "detection.ap50"], None, None, 100
    )

    assert result["series"]["loss.total"][0]["raw_name"] == "loss"
    assert result["series"]["loss.total"][0]["source"] == "mlflow"
    assert result["series"]["detection.ap50"][0]["raw_name"] == "bbox_mAP_50"
    assert result["series"]["detection.ap50"][0]["source"] == "tensorboard"


def test_paddlex_discovers_tensorboard_events_in_worker_output_directory(
    tmp_path: Path,
) -> None:
    job = _job()
    event_dir = tmp_path / "runs" / f"job-{job.id}" / "output" / "tensorboard"
    event_dir.mkdir(parents=True)
    (event_dir / "events.out.tfevents.1").touch()
    opened_paths: list[str] = []

    def event_factory(path: str):
        opened_paths.append(path)
        return PaddleEvents()

    service = TrainingObservabilityService(
        _settings(tmp_path),
        mlflow_client_factory=OfflineMlflow,
        event_accumulator_factory=event_factory,
    )

    result = service.for_engine("paddlex").scalars(
        job, ["detection.ap50"], None, None, 100
    )

    assert opened_paths == [str(event_dir)]
    assert result["series"]["detection.ap50"][0]["source"] == "tensorboard"
    assert result["availability"]["tensorboard"] == {
        "available": True,
        "reason": None,
    }


def test_paddlex_raw_log_parser_reuses_complete_coco_metric_contract() -> None:
    lines = {
        "bbox_mAP": "Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.410",
        "bbox_mAP_50": "Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.680",
        "bbox_mAP_75": "Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.430",
        "bbox_mAP_s": "Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.200",
        "bbox_mAP_m": "Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.450",
        "bbox_mAP_l": "Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.610",
        "AR": "Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.540",
    }

    for raw_name, line in lines.items():
        assert parse_log_line(line)["metrics"] == {
            raw_name: pytest.approx(float(line.rsplit("=", 1)[1]))
        }


def test_paddlex_raw_log_reader_keeps_recent_lines_with_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import visiox_api.services.training_observability as observability_module

    monkeypatch.setattr(observability_module, "_OBSERVABILITY_MAX_LINES", 2)
    job = _job()
    run_path = tmp_path / "runs" / f"job-{job.id}"
    run_path.mkdir(parents=True)
    (run_path / "stdout.log").write_text(
        "loss: 3.0\nloss: 2.0\nloss: 1.0\n", encoding="utf-8"
    )
    service = TrainingObservabilityService(
        _settings(tmp_path), mlflow_client_factory=OfflineMlflow
    )

    result = service.for_engine("paddlex").scalars(
        job, ["loss.total"], None, None, 100
    )

    assert [point["value"] for point in result["series"]["loss.total"]] == [
        2.0,
        1.0,
    ]


def test_paddlex_raw_log_fallback_preserves_epoch_and_model_loss_family(
    tmp_path: Path,
) -> None:
    job = _job()
    run_path = tmp_path / "runs" / f"job-{job.id}"
    run_path.mkdir(parents=True)
    (run_path / "stdout.log").write_text(
        "Epoch: [3] [40/100] learning_rate: 0.00025 loss: 1.75 "
        "loss_cls: 0.50 loss_bbox: 1.25\n",
        encoding="utf-8",
    )
    service = TrainingObservabilityService(
        _settings(tmp_path), mlflow_client_factory=OfflineMlflow
    )

    result = service.for_engine("paddlex").scalars(
        job,
        ["loss.total", "loss.classification", "loss.bbox"],
        None,
        None,
        100,
    )

    point = result["series"]["loss.classification"][0]
    assert point["epoch"] == 3.0
    assert point["step"] == 340.0
    assert point["source"] == "logs"
    assert point["canonical_name"] == "loss.classification"
    assert result["availability"]["logs"] == {"available": True, "reason": None}


def test_paddlex_summary_exposes_all_sources_and_secondary_actions(
    tmp_path: Path,
) -> None:
    job = _job()
    run_path = tmp_path / "runs" / f"job-{job.id}"
    run_path.mkdir(parents=True)
    (run_path / "visiox-progress.json").write_text(
        json.dumps(
            {
                "availability": {
                    "visualdl": {"available": True, "reason": None},
                },
                "resources": [],
            }
        ),
        encoding="utf-8",
    )
    (run_path / "stdout.log").write_text("training", encoding="utf-8")
    (run_path / "artifact-manifest.json").write_text(
        json.dumps({"artifacts": []}), encoding="utf-8"
    )
    service = TrainingObservabilityService(
        _settings(tmp_path), mlflow_client_factory=OfflineMlflow
    )

    summary = service.for_engine("paddlex").summary(
        job,
        SimpleNamespace(id="pipeline-1", name="pipeline", status="running", engine="paddlex"),
        SimpleNamespace(id="task-1", status="RUNNING"),
    )

    assert set(summary["availability"]) == {
        "mlflow",
        "tensorboard",
        "visualdl",
        "progress",
        "resources",
        "logs",
        "artifacts",
    }
    assert summary["availability"]["mlflow"]["available"] is False
    assert summary["availability"]["visualdl"] == {
        "available": True,
        "reason": None,
    }
    assert summary["availability"]["logs"]["available"] is True
    assert summary["secondary_actions"] == [
        {"source": "mlflow", "url": "http://mlflow.test"},
        {"source": "tensorboard", "url": "http://tensorboard.test"},
        {"source": "visualdl", "url": "http://visualdl.test"},
    ]
    assert "available_histograms" not in summary

    artifacts = service.for_engine("paddlex").artifacts(job)
    assert set(artifacts["availability"]) == {
        "mlflow",
        "tensorboard",
        "visualdl",
        "progress",
        "resources",
        "logs",
        "artifacts",
    }
