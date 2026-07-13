from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

import pytest

from visiox_api.services.training_observability import (
    TrainingObservabilityService,
    _downsample_points,
    _normalize_metric_name,
)


class FakeMlflowClient:
    def __init__(
        self,
        histories: dict[str, list[object]] | None = None,
        experiments: list[object] | None = None,
    ) -> None:
        self.histories = histories or {}
        self.search_calls: list[dict[str, object]] = []
        self.search_experiments_calls = 0
        self.experiments = experiments or [SimpleNamespace(experiment_id="1", lifecycle_stage="active")]

    def search_experiments(self) -> list[object]:
        self.search_experiments_calls += 1
        return self.experiments

    def search_runs(self, *, experiment_ids: list[str], filter_string: str) -> list[object]:
        self.search_calls.append({"experiment_ids": experiment_ids, "filter_string": filter_string})
        return [SimpleNamespace(info=SimpleNamespace(run_id="run-1"))]

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

    assert result["series"]["train.box_loss"] == [{"step": 1.0, "value": 1.0, "timestamp": 1.0}]
    assert result["series"]["metrics.map50"] == [{"step": 1.0, "value": 0.6, "timestamp": 10.0}]


def test_get_graph_parses_tensorboard_graph(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    graph = SimpleNamespace(
        node=[
            SimpleNamespace(name="input", op="Placeholder", input=[], attr={}),
            SimpleNamespace(
                name="model.0.conv",
                op="aten::_convolution",
                input=["input:0"],
                attr={"label": SimpleNamespace(s=b"Conv")},
            ),
        ]
    )
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: FakeMlflowClient(),
        event_accumulator_factory=lambda _: FakeEventAccumulator(graph=graph),
    )
    (test_settings.training_runs_root / "runs" / f"job-{fake_job.id}").mkdir(parents=True)
    (test_settings.training_runs_root / "runs" / f"job-{fake_job.id}" / "events.out.tfevents.1").touch()

    result = service.get_graph(fake_job)

    assert result["nodes"] == [{"id": "model.0.conv", "label": "Conv", "op": "aten::_convolution", "attributes": {}}]
    assert result["edges"] == [{"source": "input", "target": "model.0.conv"}]
    assert result["availability"]["tensorboard"] == {"available": True, "reason": None}


def test_get_histogram_converts_tensorboard_buckets(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    histogram = SimpleNamespace(
        step=5,
        wall_time=50.0,
        histogram_value=SimpleNamespace(min=-1.0, bucket_limit=[0.0], bucket=[12.0]),
    )
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: FakeMlflowClient(),
        event_accumulator_factory=lambda _: FakeEventAccumulator(
            histograms={"weights/model.0.conv.weight": [histogram]}
        ),
    )
    (test_settings.training_runs_root / "runs" / f"job-{fake_job.id}").mkdir(parents=True)
    (test_settings.training_runs_root / "runs" / f"job-{fake_job.id}" / "events.out.tfevents.1").touch()

    result = service.get_histogram(fake_job, "weight", "weights/model.0.conv.weight", 5)

    assert result["kind"] == "weight"
    assert result["tag"] == "weights/model.0.conv.weight"
    assert result["step"] == 5
    assert result["buckets"] == [{"lower": -1.0, "upper": 0.0, "count": 12.0}]
    assert result["availability"]["tensorboard"] == {"available": True, "reason": None}


def test_graph_returns_empty_partial_payload_when_tensorboard_is_unavailable(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    service = TrainingObservabilityService(test_settings, mlflow_client_factory=lambda _: FakeMlflowClient())

    result = service.get_graph(fake_job)

    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["availability"]["tensorboard"] == {"available": False, "reason": "event file not found"}


def test_histogram_returns_empty_partial_payload_when_tensorboard_is_unavailable(
    fake_job: SimpleNamespace,
    test_settings: SimpleNamespace,
) -> None:
    service = TrainingObservabilityService(test_settings, mlflow_client_factory=lambda _: FakeMlflowClient())

    result = service.get_histogram(fake_job, "weight", "weights/model.0.conv.weight", 5)

    assert result["kind"] == "weight"
    assert result["tag"] == "weights/model.0.conv.weight"
    assert result["step"] == 5
    assert result["buckets"] == []
    assert result["availability"]["tensorboard"] == {"available": False, "reason": "event file not found"}


def test_default_event_accumulator_retains_all_histogram_events(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert captured == {"run_path": "training-run", "size_guidance": {"histograms": 0}}


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

    assert resources["series"]["cpu_percent"] == [
        {"step": 1.0, "value": 30.0, "timestamp": 10.0},
        {"step": 2.0, "value": 40.0, "timestamp": 20.0},
    ]
    assert summary["availability"]["progress"] == {"available": True, "reason": None}
    assert summary["status"] == "succeeded"


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
