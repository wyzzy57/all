from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sys
from threading import Barrier
import time
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

import pytest

from visiox_common.settings import Settings
from visiox_api.services.training_observability import (
    TrainingObservabilityService,
    _downsample_points,
    _normalize_metric_name,
)
from visiox_api.services.observability.llamafactory import (
    LlamaFactoryObservabilityAdapter,
)
from visiox_api.services.observability.ultralytics import (
    UltralyticsObservabilityAdapter,
)


class FakeMlflowClient:
    def __init__(
        self,
        histories: dict[str, list[object]] | None = None,
        experiments: list[object] | None = None,
        runs: list[object] | None = None,
    ) -> None:
        self.histories = histories or {}
        self.search_calls: list[dict[str, object]] = []
        self.search_experiments_calls = 0
        self.experiments = experiments or [SimpleNamespace(experiment_id="1", lifecycle_stage="active")]
        self.runs = runs or [
            SimpleNamespace(
                info=SimpleNamespace(run_id="run-1"),
                data=SimpleNamespace(metrics={}),
            )
        ]

    def search_experiments(self) -> list[object]:
        self.search_experiments_calls += 1
        return self.experiments

    def search_runs(self, *, experiment_ids: list[str], filter_string: str) -> list[object]:
        self.search_calls.append({"experiment_ids": experiment_ids, "filter_string": filter_string})
        return self.runs

    def get_metric_history(self, run_id: str, key: str) -> list[object]:
        assert run_id == "run-1"
        return self.histories.get(key, [])


class FakeEventAccumulator:
    def __init__(
        self,
        scalars: dict[str, list[object]] | None = None,
        graph: object | None = None,
        histograms: dict[str, list[object]] | None = None,
    ) -> None:
        self.scalars = scalars or {}
        self.graph = graph
        self.histograms = histograms or {}
        self.reload_count = 0

    def Reload(self) -> FakeEventAccumulator:
        self.reload_count += 1
        return self

    def Tags(self) -> dict[str, list[str]]:
        return {
            "scalars": list(self.scalars),
            "histograms": list(self.histograms),
        }

    def Scalars(self, tag: str) -> list[object]:
        return self.scalars[tag]

    def Graph(self) -> object:
        if self.graph is None:
            raise ValueError("graph not found")
        return self.graph

    def Histograms(self, tag: str) -> list[object]:
        return self.histograms[tag]


@pytest.fixture
def fake_job() -> SimpleNamespace:
    return SimpleNamespace(
        id="123",
        status="succeeded",
        metrics={"observability": {"mlflow_run_name": "custom-run"}},
    )


@pytest.fixture
def test_settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        mlflow_tracking_uri="http://mlflow.test",
        training_runs_root=tmp_path,
    )


def test_normalize_ultralytics_metric_names() -> None:
    assert _normalize_metric_name("train/box_loss") == "train.box_loss"
    assert _normalize_metric_name("metrics/mAP50-95(B)") == "metrics.map50_95"
    assert _normalize_metric_name("lr/pg0") == "learning_rate"


def test_for_engine_uses_registered_ultralytics_and_llamafactory_adapters(
    test_settings: SimpleNamespace,
) -> None:
    service = TrainingObservabilityService(test_settings)

    assert isinstance(service.for_engine("yolo26"), UltralyticsObservabilityAdapter)
    assert isinstance(
        service.for_engine("llamafactory"), LlamaFactoryObservabilityAdapter
    )


@pytest.mark.parametrize(
    ("engine", "raw_name", "canonical_name", "unit", "split"),
    [
        ("yolo26", "train/box_loss", "loss.box", "loss", "train"),
        ("llamafactory", "eval/loss", "language.loss", "loss", "val"),
        ("llamafactory", "eval_loss", "language.loss", "loss", "val"),
    ],
)
def test_scalar_points_preserve_legacy_fields_and_add_canonical_metadata(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
    engine: str,
    raw_name: str,
    canonical_name: str,
    unit: str,
    split: str,
) -> None:
    normalized_name = _normalize_metric_name(raw_name)
    client = FakeMlflowClient(
        histories={raw_name: [SimpleNamespace(step=3, value=0.75, timestamp=2_000)]},
        runs=[
            SimpleNamespace(
                info=SimpleNamespace(run_id="run-1"),
                data=SimpleNamespace(metrics={raw_name: 0.75}),
            )
        ],
    )
    service = TrainingObservabilityService(
        test_settings, mlflow_client_factory=lambda _: client
    )

    result = service.for_engine(engine).scalars(
        fake_job, [normalized_name], None, None, 100
    )

    point = result["series"][normalized_name][0]
    assert {"step", "value", "timestamp"} <= point.keys()
    assert point == {
        "canonical_name": canonical_name,
        "raw_name": raw_name,
        "unit": unit,
        "split": split,
        "step": 3.0,
        "epoch": None,
        "value": 0.75,
        "timestamp": 2.0,
        "source": "mlflow",
    }


def test_downsampling_preserves_first_last_min_and_max() -> None:
    points = [
        {"step": step, "value": value, "timestamp": step * 10.0}
        for step, value in enumerate([0, 4, 1, 8, 2, 9, 3, 7, 5, 6])
    ]

    sampled = _downsample_points(points, max_points=6)

    assert sampled[0] == points[0]
    assert sampled[-1] == points[-1]
    assert {point["value"] for point in sampled}.issuperset({0, 9})
    assert len(sampled) <= 6


def test_point_limit_uses_settings_default_and_preserves_explicit_maximum(
    test_settings: SimpleNamespace,
) -> None:
    test_settings.observability_max_points = 3_500
    service = TrainingObservabilityService(test_settings)

    assert service._point_limit(None) == 3_500
    assert service._point_limit(10_000) == 10_000
    with pytest.raises(ValueError, match="max_points must be positive"):
        service._point_limit(0)


def test_llamafactory_jsonl_fallback_returns_all_metric_and_per_gpu_points(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    run_path = test_settings.training_runs_root / "runs" / "job-123"
    run_path.mkdir(parents=True)
    (run_path / "events.out.tfevents.1").touch()
    (run_path / "visiox-progress.json").write_text("{}", encoding="utf-8")
    (run_path / "visiox-metrics.jsonl").write_text(
        '\n'.join(
            (
                '{"step":1,"timestamp":1.0,"loss":1.2,"learning_rate":0.0001}',
                '{"step":2,"timestamp":2.0,"loss":0.9,"learning_rate":0.00008}',
            )
        ),
        encoding="utf-8",
    )
    (run_path / "resource_metrics.jsonl").write_text(
        '{"step":2,"timestamp":2.0,"system.cpu_percent":35,"gpus":['
        '{"uuid":"GPU-a","index":0,"utilization_percent":82,"memory_used_mb":4096}]}'
        '\n',
        encoding="utf-8",
    )
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: FakeMlflowClient(),
        event_accumulator_factory=lambda _: FakeEventAccumulator(),
    )

    scalars = service.get_scalars(
        fake_job,
        ["loss", "learning_rate"],
        None,
        None,
        100,
        engine="llamafactory",
    )
    resources = service.get_resources(
        fake_job,
        None,
        None,
        100,
        engine="llamafactory",
    )

    assert [point["value"] for point in scalars["series"]["loss"]] == [1.2, 0.9]
    assert [point["value"] for point in scalars["series"]["learning_rate"]] == [0.0001, 0.00008]
    assert resources["series"]["gpu.GPU-a.utilization_percent"][0]["value"] == 82
    assert resources["series"]["gpu.GPU-a.memory_used_mb"][0]["value"] == 4096


def test_llamafactory_jsonl_and_progress_match_canonical_query_keys(
    tmp_path: Path,
) -> None:
    job = SimpleNamespace(id="llm-canonical", metrics={})
    run_path = tmp_path / "runs" / "job-llm-canonical"
    run_path.mkdir(parents=True)
    (run_path / "visiox-metrics.jsonl").write_text(
        json.dumps(
            {
                "step": 2,
                "timestamp": 20.0,
                "train_loss": 1.5,
                "learning_rate": 0.0002,
            }
        ),
        encoding="utf-8",
    )
    (run_path / "visiox-progress.json").write_text(
        json.dumps(
            {
                "metric_samples": [
                    {"step": 3, "timestamp": 30.0, "grad_norm": 1.25}
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        training_runs_root=tmp_path,
        mlflow_tracking_uri="http://mlflow.invalid",
    )
    service = TrainingObservabilityService(
        settings,
        mlflow_client_factory=lambda _uri: (_ for _ in ()).throw(
            ConnectionError("offline")
        ),
    )

    result = service.get_scalars(
        job,
        [
            "language.loss",
            "optimization.learning_rate",
            "optimization.gradient_norm",
        ],
        None,
        None,
        100,
        engine="llamafactory",
    )

    loss_points = result["series"]["language.loss"]
    assert [(point["raw_name"], point["source"]) for point in loss_points] == [
        ("train_loss", "jsonl")
    ]
    assert result["series"]["optimization.learning_rate"][0]["raw_name"] == (
        "learning_rate"
    )
    assert result["series"]["optimization.gradient_norm"][0]["raw_name"] == (
        "grad_norm"
    )
    assert result["series"]["optimization.gradient_norm"][0]["source"] == (
        "progress"
    )


def test_for_pipeline_prefers_framework_and_rejects_unknown_identity(
    test_settings: SimpleNamespace,
) -> None:
    service = TrainingObservabilityService(test_settings)
    test_settings.llm_training_image_digest = ""
    test_settings.paddlex_training_image_digest = ""
    test_settings.paddlex_inference_image_digest = ""
    test_settings.ultralytics_training_image_digest = ""
    test_settings.deployment_image_digest = ""

    adapter = service.for_pipeline(
        SimpleNamespace(
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version=None,
            engine="yolo26",
        )
    )

    assert adapter.engine == "paddlex"
    with pytest.raises(ValueError, match="Unknown observability framework"):
        service.for_pipeline(
            SimpleNamespace(framework="mystery", adapter_key=None, engine="yolo26")
        )
    with pytest.raises(ValueError, match="does not belong to framework"):
        service.for_pipeline(
            SimpleNamespace(
                framework="paddlex",
                adapter_key="ultralytics.object_detection.v1",
                engine="yolo26",
            )
        )


def test_jsonl_reader_keeps_recent_lines_with_configured_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import visiox_api.services.training_observability as observability_module

    monkeypatch.setattr(observability_module, "_OBSERVABILITY_MAX_LINES", 2)
    monkeypatch.setattr(observability_module, "_OBSERVABILITY_MAX_FILE_BYTES", 256)
    job = SimpleNamespace(id="bounded-jsonl", metrics={})
    run_path = tmp_path / "runs" / "job-bounded-jsonl"
    run_path.mkdir(parents=True)
    (run_path / "visiox-metrics.jsonl").write_text(
        "\n".join(json.dumps({"step": step}) for step in range(5)) + "\n",
        encoding="utf-8",
    )
    service = TrainingObservabilityService(
        SimpleNamespace(training_runs_root=tmp_path)
    )

    assert service._jsonl_samples(job, "visiox-metrics.jsonl") == [
        {"step": 3},
        {"step": 4},
    ]


def test_jsonl_reader_discards_partial_old_content_at_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import visiox_api.services.training_observability as observability_module

    monkeypatch.setattr(observability_module, "_OBSERVABILITY_MAX_FILE_BYTES", 32)
    job = SimpleNamespace(id="bounded-jsonl-bytes", metrics={})
    run_path = tmp_path / "runs" / "job-bounded-jsonl-bytes"
    run_path.mkdir(parents=True)
    (run_path / "visiox-metrics.jsonl").write_text(
        json.dumps({"old": "x" * 100})
        + "\n"
        + json.dumps({"step": 8})
        + "\n"
        + json.dumps({"step": 9})
        + "\n",
        encoding="utf-8",
    )
    service = TrainingObservabilityService(
        SimpleNamespace(training_runs_root=tmp_path)
    )

    assert service._jsonl_samples(job, "visiox-metrics.jsonl") == [
        {"step": 8},
        {"step": 9},
    ]


def test_scalars_merge_mlflow_history_without_fabricating_missing_series(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    fake_mlflow_client = FakeMlflowClient(
        {
            "train/box_loss": [
                SimpleNamespace(step=2, value=0.9, timestamp=2_000),
                SimpleNamespace(step=1, value=1.4, timestamp=1_000),
            ]
        }
    )
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: fake_mlflow_client,
    )

    result = service.get_scalars(fake_job, ["train.box_loss", "metrics.map50"], None, None, 2_000)

    assert [point["value"] for point in result["series"]["train.box_loss"]] == [1.4, 0.9]
    assert result["series"]["metrics.map50"] == []
    assert result["availability"]["mlflow"]["available"] is True
    assert fake_mlflow_client.search_calls[0] == {
        "experiment_ids": ["1"],
        "filter_string": "tags.mlflow.runName = 'custom-run'",
    }


def test_mlflow_scalar_query_matches_the_strict_client_signature(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    client = FakeMlflowClient({"train/box_loss": [SimpleNamespace(step=1, value=1.4, timestamp=1_000)]})
    service = TrainingObservabilityService(test_settings, mlflow_client_factory=lambda _: client)

    result = service.get_scalars(fake_job, ["train.box_loss"], None, None, 2_000)

    assert result["availability"]["mlflow"] == {"available": True, "reason": None}
    assert client.search_experiments_calls == 1
    assert client.search_calls == [
        {"experiment_ids": ["1"], "filter_string": "tags.mlflow.runName = 'custom-run'"}
    ]


def test_mlflow_scalar_query_uses_only_active_experiment_ids(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    client = FakeMlflowClient(
        experiments=[
            SimpleNamespace(experiment_id="1", lifecycle_stage="active"),
            SimpleNamespace(experiment_id="2", lifecycle_stage="deleted"),
            SimpleNamespace(experiment_id="3", lifecycle_stage="active"),
        ]
    )
    service = TrainingObservabilityService(test_settings, mlflow_client_factory=lambda _: client)

    result = service.get_scalars(fake_job, ["train.box_loss"], None, None, 2_000)

    assert result["availability"]["mlflow"] == {"available": True, "reason": None}
    assert client.search_calls == [
        {"experiment_ids": ["1", "3"], "filter_string": "tags.mlflow.runName = 'custom-run'"}
    ]


def test_mlflow_returns_empty_runs_without_calling_search_when_no_active_experiments(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    client = FakeMlflowClient(experiments=[SimpleNamespace(experiment_id="2", lifecycle_stage="deleted")])
    service = TrainingObservabilityService(test_settings, mlflow_client_factory=lambda _: client)

    result = service.get_scalars(fake_job, ["train.box_loss"], None, None, 2_000)

    assert result["series"] == {"train.box_loss": []}
    assert result["availability"]["mlflow"] == {"available": True, "reason": None}
    assert client.search_calls == []


def test_mlflow_failure_returns_tensorboard_data_and_reason(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    fake_event_accumulator = FakeEventAccumulator(
        scalars={"train/box_loss": [SimpleNamespace(step=1, value=1.4, wall_time=10.0)]}
    )
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: (_ for _ in ()).throw(ConnectionError("offline")),
        event_accumulator_factory=lambda _: fake_event_accumulator,
    )
    (test_settings.training_runs_root / "runs" / f"job-{fake_job.id}").mkdir(parents=True)
    (test_settings.training_runs_root / "runs" / f"job-{fake_job.id}" / "events.out.tfevents.1").touch()

    result = service.get_scalars(fake_job, ["train.box_loss"], None, None, 2_000)

    assert result["series"]["train.box_loss"]
    assert result["availability"]["mlflow"] == {"available": False, "reason": "offline"}
    assert result["availability"]["tensorboard"] == {"available": True, "reason": None}


def test_tensorboard_only_fills_series_missing_from_mlflow(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    mlflow_client = FakeMlflowClient(
        {"train/box_loss": [SimpleNamespace(step=1, value=1.0, timestamp=1_000)]}
    )
    event_accumulator = FakeEventAccumulator(
        scalars={
            "train/box_loss": [SimpleNamespace(step=1, value=99.0, wall_time=10.0)],
            "metrics/mAP50(B)": [SimpleNamespace(step=1, value=0.6, wall_time=10.0)],
        }
    )
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: mlflow_client,
        event_accumulator_factory=lambda _: event_accumulator,
    )
    (test_settings.training_runs_root / "runs" / f"job-{fake_job.id}").mkdir(parents=True)
    (test_settings.training_runs_root / "runs" / f"job-{fake_job.id}" / "events.out.tfevents.1").touch()

    result = service.get_scalars(fake_job, ["train.box_loss", "metrics.map50"], None, None, 2_000)

    train_point = result["series"]["train.box_loss"][0]
    validation_point = result["series"]["metrics.map50"][0]
    assert {key: train_point[key] for key in ("step", "value", "timestamp")} == {
        "step": 1.0,
        "value": 1.0,
        "timestamp": 1.0,
    }
    assert train_point["raw_name"] == "train/box_loss"
    assert train_point["source"] == "mlflow"
    assert {
        key: validation_point[key] for key in ("step", "value", "timestamp")
    } == {"step": 1.0, "value": 0.6, "timestamp": 10.0}
    assert validation_point["raw_name"] == "metrics/mAP50(B)"
    assert validation_point["source"] == "tensorboard"


def test_default_event_accumulator_retains_all_scalar_events(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    event_module = ModuleType("tensorboard.backend.event_processing.event_accumulator")

    class RecordedEventAccumulator:
        def __init__(self, run_path: str, *, size_guidance: dict[str, int]) -> None:
            captured["run_path"] = run_path
            captured["size_guidance"] = size_guidance

    event_module.EventAccumulator = RecordedEventAccumulator
    monkeypatch.setitem(sys.modules, "tensorboard.backend.event_processing.event_accumulator", event_module)

    accumulator = TrainingObservabilityService._default_event_accumulator("training-run")

    assert isinstance(accumulator, RecordedEventAccumulator)
    assert captured == {"run_path": "training-run", "size_guidance": {"scalars": 0}}


def test_resources_read_progress_snapshot_and_summary_reports_source_availability(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    run_path = test_settings.training_runs_root / "runs" / f"job-{fake_job.id}"
    run_path.mkdir(parents=True)
    (run_path / "visiox-progress.json").write_text(
        json.dumps(
            {
                "resources": [
                    {"step": 2, "cpu_percent": 40.0, "gpu_memory_mb": 1024.0, "timestamp": 20.0},
                    {"step": 1, "cpu_percent": 30.0, "gpu_memory_mb": 512.0, "timestamp": 10.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    service = TrainingObservabilityService(test_settings, mlflow_client_factory=lambda _: FakeMlflowClient())

    resources = service.get_resources(fake_job, None, None, 2_000)
    summary = service.get_summary(fake_job, SimpleNamespace(name="pipeline"), SimpleNamespace(status="running"))

    cpu_points = resources["series"]["cpu_percent"]
    assert [point["value"] for point in cpu_points] == [30.0, 40.0]
    assert cpu_points[0]["canonical_name"] == "system.cpu.utilization"
    assert cpu_points[0]["raw_name"] == "cpu_percent"
    assert cpu_points[0]["unit"] == "percent"
    assert cpu_points[0]["source"] == "progress"
    assert summary["availability"]["progress"] == {"available": True, "reason": None}
    assert summary["status"] == "succeeded"


def test_summary_reads_training_tags_without_advertising_dedicated_resource_scalars(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    run_path = test_settings.training_runs_root / "runs" / f"job-{fake_job.id}"
    run_path.mkdir(parents=True)
    (run_path / "events.out.tfevents.1").touch()
    (run_path / "visiox-progress.json").write_text(
        json.dumps(
            {
                "progress": {"current_epoch": 3, "total_epochs": 8, "percent": 37.5},
                "timing": {"elapsed_seconds": 18.0, "eta_seconds": 30.0},
                "environment": {"device": "cpu"},
                "latest_metrics": {"metrics/mAP50(B)": 0.61},
                "resources": [
                    {"step": 3, "timestamp": 18.0, "system.cpu_percent": 37.5},
                ],
            }
        ),
        encoding="utf-8",
    )
    mlflow_client = FakeMlflowClient(
        runs=[
            SimpleNamespace(
                info=SimpleNamespace(run_id="run-1"),
                data=SimpleNamespace(metrics={"train/box_loss": 0.82, "metrics/mAP50(B)": 0.59}),
            )
        ]
    )
    accumulator = FakeEventAccumulator(
        scalars={
            "val/box_loss": [],
            "system.cpu_percent": [],
            "train/images_per_second": [],
        },
        histograms={
            "weights/model.0.conv.weight": [],
            "gradients/model.0.conv.weight": [],
        },
    )
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: mlflow_client,
        event_accumulator_factory=lambda _: accumulator,
    )

    summary = service.get_summary(
        fake_job,
        SimpleNamespace(id="pipeline-1", name="pipeline", status="running"),
        SimpleNamespace(id="task-1", status="RUNNING"),
    )
    resources = service.get_resources(fake_job, None, None, 2_000)

    assert summary["progress"] == {"current_epoch": 3, "total_epochs": 8, "percent": 37.5}
    assert summary["timing"] == {"elapsed_seconds": 18.0, "eta_seconds": 30.0}
    assert summary["environment"] == {"device": "cpu"}
    assert summary["latest_metrics"] == {
        "train/box_loss": 0.82,
        "metrics/mAP50(B)": 0.61,
    }
    assert summary["available_scalar_keys"] == [
        "metrics.map50",
        "train.box_loss",
        "val.box_loss",
    ]
    resource_point = resources["series"]["system.cpu_percent"][0]
    assert {
        key: resource_point[key] for key in ("step", "value", "timestamp")
    } == {"step": 3.0, "value": 37.5, "timestamp": 18.0}
    assert resource_point["canonical_name"] == "system.cpu.utilization"
    assert resource_point["source"] == "progress"
    assert "available_histograms" not in summary
    assert summary["availability"]["progress"] == {"available": True, "reason": None}
    assert summary["availability"]["tensorboard"] == {"available": True, "reason": None}


def test_summary_and_data_endpoints_agree_when_progress_and_events_are_corrupt(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    run_path = test_settings.training_runs_root / "runs" / f"job-{fake_job.id}"
    run_path.mkdir(parents=True)
    (run_path / "events.out.tfevents.1").write_bytes(b"truncated")
    (run_path / "visiox-progress.json").write_text("{broken", encoding="utf-8")

    class CorruptEventAccumulator(FakeEventAccumulator):
        def Reload(self) -> FakeEventAccumulator:
            raise ValueError("corrupt event stream")

    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: FakeMlflowClient(),
        event_accumulator_factory=lambda _: CorruptEventAccumulator(),
    )

    summary = service.get_summary(fake_job, SimpleNamespace(), SimpleNamespace())
    resources = service.get_resources(fake_job, None, None, 2_000)
    assert summary["availability"]["progress"]["available"] is False
    assert resources["availability"]["progress"] == summary["availability"]["progress"]
    assert summary["availability"]["tensorboard"] == {
        "available": False,
        "reason": "corrupt event stream",
    }


def test_summary_and_resources_report_mlflow_probe_failures_without_crashing(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    run_path = test_settings.training_runs_root / "runs" / f"job-{fake_job.id}"
    run_path.mkdir(parents=True)
    (run_path / "visiox-progress.json").write_text(json.dumps({"resources": []}), encoding="utf-8")
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: (_ for _ in ()).throw(ConnectionError("offline")),
    )

    summary = service.get_summary(fake_job, SimpleNamespace(), SimpleNamespace())
    resources = service.get_resources(fake_job, None, None, 2_000)

    assert summary["availability"]["mlflow"] == {"available": False, "reason": "offline"}
    assert resources["availability"]["mlflow"] == {"available": False, "reason": "offline"}


def test_event_accumulator_cache_is_keyed_by_latest_event_mtime(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    run_path = test_settings.training_runs_root / "runs" / f"job-{fake_job.id}"
    run_path.mkdir(parents=True)
    event_file = run_path / "events.out.tfevents.1"
    event_file.touch()
    accumulators = [FakeEventAccumulator(), FakeEventAccumulator()]
    service = TrainingObservabilityService(
        test_settings,
        event_accumulator_factory=lambda _: accumulators.pop(0),
    )

    assert service._event_accumulator(fake_job) is service._event_accumulator(fake_job)
    event_file.touch()
    assert service._event_accumulator(fake_job) is not None
    assert len(accumulators) == 0


def test_event_accumulator_cache_honors_configured_size_and_evicts_lru(
    test_settings: SimpleNamespace,
) -> None:
    test_settings.observability_event_cache_size = 1
    jobs = [SimpleNamespace(id="job-a"), SimpleNamespace(id="job-b")]
    for job in jobs:
        run_path = test_settings.training_runs_root / "runs" / f"job-{job.id}"
        run_path.mkdir(parents=True)
        (run_path / "events.out.tfevents.1").touch()
    created: list[FakeEventAccumulator] = []

    def create_accumulator(_: str) -> FakeEventAccumulator:
        accumulator = FakeEventAccumulator()
        created.append(accumulator)
        return accumulator

    service = TrainingObservabilityService(test_settings, event_accumulator_factory=create_accumulator)

    service._event_accumulator(jobs[0])
    service._event_accumulator(jobs[1])
    service._event_accumulator(jobs[0])

    assert len(created) == 3
    assert len(service._event_accumulators) == 1


def test_event_accumulator_size_one_cache_serializes_concurrent_reload_and_publication(
    test_settings: SimpleNamespace,
) -> None:
    test_settings.observability_event_cache_size = 1
    first_job = SimpleNamespace(id="job-a")
    concurrent_job = SimpleNamespace(id="job-b")
    for job in (first_job, concurrent_job):
        run_path = test_settings.training_runs_root / "runs" / f"job-{job.id}"
        run_path.mkdir(parents=True)
        (run_path / "events.out.tfevents.1").touch()
    created_for: list[str] = []

    def create_accumulator(run_path: str) -> FakeEventAccumulator:
        created_for.append(run_path)
        if run_path.endswith("job-job-b"):
            time.sleep(0.05)
        return FakeEventAccumulator()

    service = TrainingObservabilityService(test_settings, event_accumulator_factory=create_accumulator)
    service._event_accumulator(first_job)
    barrier = Barrier(8)

    def load_concurrently() -> FakeEventAccumulator:
        barrier.wait(timeout=2)
        return service._event_accumulator(concurrent_job)

    with ThreadPoolExecutor(max_workers=8) as executor:
        accumulators = list(executor.map(lambda _: load_concurrently(), range(8)))

    assert len({id(accumulator) for accumulator in accumulators}) == 1
    assert sum(path.endswith("job-job-b") for path in created_for) == 1
    assert len(service._event_accumulators) == 1


def test_default_settings_keep_job_and_metrics_paths_inside_shared_run_root() -> None:
    settings = Settings.model_construct()
    job = SimpleNamespace(
        id="abc",
        metrics={"observability": {"tensorboard_run_name": "../../outside"}},
    )

    run_path = TrainingObservabilityService(settings)._run_path(job)

    assert settings.training_runs_root == Path("/workspace/training-runs")
    assert settings.observability_max_points == 2_000
    assert settings.observability_event_cache_size == 32
    assert settings.observability_live_poll_seconds == 5
    assert run_path == Path("/workspace/training-runs/runs/job-abc").resolve()
    assert settings.training_runs_root.resolve() in run_path.parents


@pytest.mark.parametrize("job_id", ["../outside", "child/job", "child\\job"])
def test_run_path_rejects_traversal_and_separators(
    job_id: str,
    test_settings: SimpleNamespace,
) -> None:
    service = TrainingObservabilityService(test_settings)

    with pytest.raises(Exception, match="invalid training job id"):
        service._run_path(SimpleNamespace(id=job_id))


def test_run_path_rejects_symlink_that_escapes_configured_root(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
    tmp_path: Path,
) -> None:
    test_settings.training_runs_root = tmp_path / "configured-root"
    run_root = test_settings.training_runs_root / "runs"
    run_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = run_root / f"job-{fake_job.id}"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are not permitted: {exc}")

    service = TrainingObservabilityService(test_settings)

    with pytest.raises(Exception, match="training run path is outside the configured root"):
        service._run_path(fake_job)
