from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from pathlib import Path
from threading import Lock
from typing import Any
import math
import re

from visiox_common.settings import Settings


_DEFAULT_MAX_POINTS = 2_000
_RESOURCE_SCALAR_KEYS = frozenset(
    {
        "system.cpu_percent",
        "system.memory_percent",
        "system.memory_used_gb",
        "system.gpu_utilization_percent",
        "system.gpu_memory_used_gb",
        "system.gpu_memory_reserved_gb",
        "train.images_per_second",
    }
)
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9-]+$")


class ObservabilitySourceError(RuntimeError):
    def __init__(self, source: str, message: str) -> None:
        self.source = source
        self.message = message
        super().__init__(message)


def _normalize_metric_name(name: str) -> str:
    normalized = name.strip().lower().replace("/", ".")
    normalized = normalized.replace("mAP", "map")
    normalized = re.sub(r"\([^)]*\)", "", normalized)
    normalized = normalized.replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_.]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._")
    if normalized in {"lr.pg0", "lr.pg1", "lr.pg2"}:
        return "learning_rate"
    return normalized


def _downsample_points(points: list[dict[str, float]], max_points: int) -> list[dict[str, float]]:
    if max_points < 1:
        raise ValueError("max_points must be positive")
    if len(points) <= max_points:
        return list(points)
    if max_points == 1:
        return [points[0]]
    if max_points == 2:
        return [points[0], points[-1]]

    interior = points[1:-1]
    available = max_points - 2
    bucket_count = max(1, math.ceil(available / 2))
    candidates: list[int] = []
    for bucket in range(bucket_count):
        start = math.floor(bucket * len(interior) / bucket_count)
        end = math.floor((bucket + 1) * len(interior) / bucket_count)
        chunk = interior[start:end]
        if not chunk:
            continue
        minimum = min(range(len(chunk)), key=lambda index: chunk[index]["value"])
        maximum = max(range(len(chunk)), key=lambda index: chunk[index]["value"])
        candidates.extend((start + minimum, start + maximum))

    selected = sorted(set(candidates))[:available]
    if len(selected) < available:
        remaining = [index for index in range(len(interior)) if index not in selected]
        selected.extend(remaining[: available - len(selected)])
        selected.sort()
    return [points[0], *(interior[index] for index in selected), points[-1]]


class TrainingObservabilityService:
    def __init__(
        self,
        settings: Settings,
        *,
        mlflow_client_factory: Callable[[str], Any] | None = None,
        event_accumulator_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self._mlflow_client_factory = mlflow_client_factory or self._default_mlflow_client
        self._event_accumulator_factory = event_accumulator_factory or self._default_event_accumulator
        self._event_accumulators: OrderedDict[tuple[Path, int], Any] = OrderedDict()
        self._event_accumulator_lock = Lock()

    def get_summary(self, job: Any, pipeline: Any, task: Any) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        try:
            snapshot = self._progress_snapshot(job)
            progress_availability = (True, None)
        except ObservabilitySourceError as exc:
            progress_availability = (False, exc.message)

        scalar_keys: set[str] = set()
        latest_metrics: dict[str, float] = {}
        try:
            _, runs = self._mlflow_client_and_runs(job)
            mlflow_availability = (True, None)
        except ObservabilitySourceError as exc:
            runs = []
            mlflow_availability = (False, exc.message)
        if runs:
            metrics = getattr(getattr(runs[0], "data", None), "metrics", {})
            if isinstance(metrics, dict):
                for name, value in metrics.items():
                    if isinstance(value, int | float):
                        latest_metrics[str(name)] = float(value)
                        scalar_keys.add(_normalize_metric_name(str(name)))

        available_histograms: dict[str, list[str]] = {"weight": [], "gradient": []}
        try:
            tags = self._event_accumulator(job).Tags()
            if not isinstance(tags, dict):
                raise ObservabilitySourceError("tensorboard", "event tags are invalid")
            tensorboard_availability = (True, None)
        except ObservabilitySourceError as exc:
            tags = {}
            tensorboard_availability = (False, exc.message)
        except Exception as exc:
            tags = {}
            tensorboard_availability = (False, str(exc))
        for tag in tags.get("scalars", []):
            scalar_keys.add(_normalize_metric_name(str(tag)))
        for tag in tags.get("histograms", []):
            tag_name = str(tag)
            if tag_name.startswith("weights/"):
                available_histograms["weight"].append(tag_name)
            elif tag_name.startswith("gradients/"):
                available_histograms["gradient"].append(tag_name)

        snapshot_metrics = snapshot.get("latest_metrics")
        if isinstance(snapshot_metrics, dict):
            for name, value in snapshot_metrics.items():
                if isinstance(value, int | float):
                    latest_metrics[str(name)] = float(value)
                    scalar_keys.add(_normalize_metric_name(str(name)))
        return {
            "job_id": str(job.id),
            "status": getattr(job, "status", None),
            "pipeline": {
                "id": getattr(pipeline, "id", None),
                "name": getattr(pipeline, "name", None),
                "status": getattr(pipeline, "status", None),
            },
            "task": {
                "id": getattr(task, "id", None),
                "status": getattr(task, "status", None),
            },
            "progress": snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {},
            "timing": snapshot.get("timing") if isinstance(snapshot.get("timing"), dict) else {},
            "environment": snapshot.get("environment") if isinstance(snapshot.get("environment"), dict) else {},
            "latest_metrics": latest_metrics,
            "available_scalar_keys": sorted(
                key for key in scalar_keys if key and key not in _RESOURCE_SCALAR_KEYS
            ),
            "available_histograms": {
                kind: sorted(set(values)) for kind, values in available_histograms.items()
            },
            "availability": self._availability(
                mlflow=mlflow_availability,
                tensorboard=tensorboard_availability,
                progress=progress_availability,
            ),
        }

    def get_scalars(
        self,
        job: Any,
        keys: list[str],
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
    ) -> dict[str, Any]:
        normalized_keys = list(dict.fromkeys(_normalize_metric_name(key) for key in keys))
        series = {key: [] for key in normalized_keys}
        mlflow_available = True
        mlflow_reason: str | None = None
        try:
            for key, points in self._mlflow_scalars(job, normalized_keys).items():
                series[key] = points
        except ObservabilitySourceError as exc:
            mlflow_available = False
            mlflow_reason = exc.message

        tensorboard_available = True
        tensorboard_reason: str | None = None
        try:
            accumulator = self._event_accumulator(job)
            tags = accumulator.Tags().get("scalars", [])
            for tag in tags:
                normalized_tag = _normalize_metric_name(tag)
                if normalized_tag not in series or series[normalized_tag]:
                    continue
                series[normalized_tag] = self._scalar_points(accumulator.Scalars(tag), start_step, end_step)
        except ObservabilitySourceError as exc:
            tensorboard_available = False
            tensorboard_reason = exc.message
        except Exception as exc:
            tensorboard_available = False
            tensorboard_reason = str(exc)

        if not any(series.values()):
            try:
                samples = self._progress_snapshot(job).get("metric_samples", [])
                if isinstance(samples, list):
                    for sample in samples:
                        if not isinstance(sample, dict):
                            continue
                        step = sample.get("step")
                        timestamp = sample.get("timestamp")
                        if not isinstance(step, int | float) or not isinstance(timestamp, int | float):
                            continue
                        for name, value in sample.items():
                            normalized = _normalize_metric_name(str(name))
                            if normalized in series and isinstance(value, int | float):
                                series[normalized].append(
                                    {"step": float(step), "value": float(value), "timestamp": float(timestamp)}
                                )
            except ObservabilitySourceError:
                pass

        limit = self._point_limit(max_points)
        for key, points in series.items():
            filtered = self._filter_points(points, start_step, end_step)
            series[key] = _downsample_points(filtered, limit)
        return {
            "series": series,
            "availability": self._availability(
                mlflow=(mlflow_available, mlflow_reason),
                tensorboard=(tensorboard_available, tensorboard_reason),
                progress=self._progress_availability(job),
            ),
        }

    def get_resources(
        self,
        job: Any,
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
    ) -> dict[str, Any]:
        progress_available = True
        progress_reason: str | None = None
        series: dict[str, list[dict[str, float]]] = {}
        try:
            snapshot = self._progress_snapshot(job)
            samples = snapshot.get("resources") or snapshot.get("resource_metrics") or []
            if isinstance(samples, dict):
                samples = samples.get("points") or samples.get("samples") or []
            if not isinstance(samples, list):
                raise ObservabilitySourceError("progress", "resource samples are invalid")
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                step = sample.get("step")
                timestamp = sample.get("timestamp")
                if not isinstance(step, (int, float)) or not isinstance(timestamp, (int, float)):
                    continue
                for name, value in sample.items():
                    if name in {"step", "timestamp"} or not isinstance(value, (int, float)):
                        continue
                    series.setdefault(name, []).append(
                        {"step": float(step), "value": float(value), "timestamp": float(timestamp)}
                    )
        except ObservabilitySourceError as exc:
            progress_available = False
            progress_reason = exc.message

        limit = self._point_limit(max_points)
        for name, points in series.items():
            series[name] = _downsample_points(self._filter_points(points, start_step, end_step), limit)
        return {
            "series": series,
            "availability": self._availability(
                mlflow=self._mlflow_availability(job),
                tensorboard=self._event_availability(job),
                progress=(progress_available, progress_reason),
            ),
        }

    def get_graph(self, job: Any) -> dict[str, Any]:
        tensorboard_available = True
        tensorboard_reason: str | None = None
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        try:
            graph = self._event_accumulator(job).Graph()
        except ObservabilitySourceError as exc:
            tensorboard_available = False
            tensorboard_reason = exc.message
        except Exception as exc:
            tensorboard_available = False
            tensorboard_reason = str(exc)
        else:
            for node in getattr(graph, "node", []):
                if getattr(node, "op", "") == "Placeholder":
                    continue
                node_id = str(getattr(node, "name", ""))
                label = self._graph_label(node)
                nodes.append(
                    {
                        "id": node_id,
                        "label": label,
                        "op": str(getattr(node, "op", "")),
                        "attributes": {},
                    }
                )
                for input_name in getattr(node, "input", []):
                    source = str(input_name).lstrip("^").split(":", 1)[0]
                    if source:
                        edges.append({"source": source, "target": node_id})
        return {
            "nodes": nodes,
            "edges": edges,
            "availability": self._availability(
                mlflow=self._mlflow_availability(job),
                tensorboard=(tensorboard_available, tensorboard_reason),
                progress=self._progress_availability(job),
            ),
        }

    def get_histogram(self, job: Any, kind: str, tag: str, step: int) -> dict[str, Any]:
        buckets: list[dict[str, float]] = []
        tensorboard_available = True
        tensorboard_reason: str | None = None
        try:
            accumulator = self._event_accumulator(job)
            events = accumulator.Histograms(tag)
            event = next((candidate for candidate in events if int(candidate.step) == step), None)
            if event is None:
                raise ObservabilitySourceError("tensorboard", "histogram step not found")
            value = event.histogram_value
            lower = float(getattr(value, "min", -1.0))
            for upper, count in zip(getattr(value, "bucket_limit", []), getattr(value, "bucket", []), strict=True):
                buckets.append({"lower": lower, "upper": float(upper), "count": float(count)})
                lower = float(upper)
        except ObservabilitySourceError as exc:
            tensorboard_available = False
            tensorboard_reason = exc.message
        except Exception as exc:
            tensorboard_available = False
            tensorboard_reason = str(exc)
        return {
            "kind": kind,
            "tag": tag,
            "step": step,
            "buckets": buckets,
            "availability": self._availability(
                mlflow=self._mlflow_availability(job),
                tensorboard=(tensorboard_available, tensorboard_reason),
                progress=self._progress_availability(job),
            ),
        }

    def _mlflow_scalars(self, job: Any, keys: list[str]) -> dict[str, list[dict[str, float]]]:
        client, runs = self._mlflow_client_and_runs(job)
        if not runs:
            return {key: [] for key in keys}

        run = runs[0]
        run_id = str(run.info.run_id)
        metric_names = self._mlflow_metric_names(run, keys)
        result = {key: [] for key in keys}
        for normalized_key, names in metric_names.items():
            for name in names:
                try:
                    history = client.get_metric_history(run_id, name)
                except Exception as exc:
                    raise ObservabilitySourceError("mlflow", str(exc)) from exc
                points = [
                    {"step": float(item.step), "value": float(item.value), "timestamp": float(item.timestamp) / 1_000}
                    for item in history
                ]
                if points:
                    result[normalized_key] = points
                    break
        return result

    def _mlflow_availability(self, job: Any) -> tuple[bool, str | None]:
        try:
            self._mlflow_client_and_runs(job)
        except ObservabilitySourceError as exc:
            return False, exc.message
        return True, None

    def _mlflow_client_and_runs(self, job: Any) -> tuple[Any, list[Any]]:
        try:
            client = self._mlflow_client_factory(str(getattr(self.settings, "mlflow_tracking_uri", "")))
            experiment_ids = [
                str(experiment.experiment_id)
                for experiment in client.search_experiments()
                if getattr(experiment, "lifecycle_stage", None) == "active"
            ]
            if not experiment_ids:
                return client, []
            run_name = self._mlflow_run_name(job)
            runs = client.search_runs(
                experiment_ids=experiment_ids,
                filter_string=f"tags.mlflow.runName = '{run_name}'",
            )
        except Exception as exc:
            raise ObservabilitySourceError("mlflow", str(exc)) from exc
        return client, list(runs)

    def _event_accumulator(self, job: Any) -> Any:
        run_path = self._run_path(job)
        event_files = list(run_path.glob("events.out.tfevents.*"))
        if not event_files:
            raise ObservabilitySourceError("tensorboard", "event file not found")
        newest_mtime = max(path.stat().st_mtime_ns for path in event_files)
        key = (run_path, newest_mtime)
        with self._event_accumulator_lock:
            accumulator = self._event_accumulators.get(key)
            if accumulator is None:
                try:
                    accumulator = self._event_accumulator_factory(str(run_path))
                    accumulator.Reload()
                except Exception as exc:
                    raise ObservabilitySourceError("tensorboard", str(exc)) from exc
                self._event_accumulators[key] = accumulator
                self._event_accumulators.move_to_end(key)
                cache_size = max(0, int(getattr(self.settings, "observability_event_cache_size", 32)))
                while len(self._event_accumulators) > cache_size:
                    self._event_accumulators.popitem(last=False)
            else:
                self._event_accumulators.move_to_end(key)
            return accumulator

    def _progress_snapshot(self, job: Any) -> dict[str, Any]:
        path = self._run_path(job) / "visiox-progress.json"
        if not path.is_file():
            metrics = getattr(job, "metrics", {})
            snapshot = metrics.get("observability_snapshot") if isinstance(metrics, dict) else None
            if isinstance(snapshot, dict):
                return snapshot
            raise ObservabilitySourceError("progress", "progress snapshot not found")
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ObservabilitySourceError("progress", str(exc)) from exc
        if not isinstance(data, dict):
            raise ObservabilitySourceError("progress", "progress snapshot is invalid")
        return data

    def _run_path(self, job: Any) -> Path:
        job_id = str(getattr(job, "id", ""))
        if not _SAFE_JOB_ID.fullmatch(job_id):
            raise ObservabilitySourceError("progress", "invalid training job id")
        root = Path(getattr(self.settings, "training_runs_root", "/workspace/training-runs")).resolve()
        run_path = (root / "runs" / f"job-{job_id}").resolve()
        if root not in run_path.parents:
            raise ObservabilitySourceError("progress", "training run path is outside the configured root")
        return run_path

    @staticmethod
    def _default_mlflow_client(tracking_uri: str) -> Any:
        from mlflow import MlflowClient

        return MlflowClient(tracking_uri=tracking_uri)

    @staticmethod
    def _default_event_accumulator(run_path: str) -> Any:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

        return EventAccumulator(run_path, size_guidance={"histograms": 0})

    def _point_limit(self, max_points: int | None) -> int:
        limit = getattr(self.settings, "observability_max_points", _DEFAULT_MAX_POINTS)
        if max_points is not None:
            limit = max_points
        if limit < 1:
            raise ValueError("max_points must be positive")
        return limit

    @staticmethod
    def _filter_points(
        points: Iterable[dict[str, float]], start_step: int | None, end_step: int | None
    ) -> list[dict[str, float]]:
        return sorted(
            (
                point
                for point in points
                if (start_step is None or point["step"] >= start_step)
                and (end_step is None or point["step"] <= end_step)
            ),
            key=lambda point: (point["step"], point["timestamp"]),
        )

    @staticmethod
    def _scalar_points(events: Iterable[Any], start_step: int | None, end_step: int | None) -> list[dict[str, float]]:
        points = [
            {"step": float(event.step), "value": float(event.value), "timestamp": float(event.wall_time)}
            for event in events
        ]
        return TrainingObservabilityService._filter_points(points, start_step, end_step)

    @staticmethod
    def _mlflow_run_name(job: Any) -> str:
        metrics = getattr(job, "metrics", {})
        if isinstance(metrics, dict):
            observability = metrics.get("observability")
            if isinstance(observability, dict) and isinstance(observability.get("mlflow_run_name"), str):
                return observability["mlflow_run_name"]
        return f"job-{job.id}"

    @staticmethod
    def _mlflow_metric_names(run: Any, keys: list[str]) -> dict[str, list[str]]:
        names = {key: [key, key.replace(".", "/", 1)] for key in keys}
        metrics = getattr(getattr(run, "data", None), "metrics", {})
        if not isinstance(metrics, dict):
            return names
        for name in metrics:
            normalized = _normalize_metric_name(str(name))
            if normalized in names and name not in names[normalized]:
                names[normalized].insert(0, str(name))
        return names

    @staticmethod
    def _graph_label(node: Any) -> str:
        label = getattr(node, "name", "")
        attribute = getattr(node, "attr", {}).get("label") if hasattr(getattr(node, "attr", {}), "get") else None
        encoded = getattr(attribute, "s", None)
        if isinstance(encoded, bytes):
            return encoded.decode("utf-8", errors="replace")
        return str(label)

    def _event_availability(self, job: Any) -> tuple[bool, str | None]:
        try:
            self._event_accumulator(job)
        except ObservabilitySourceError as exc:
            return False, exc.message
        return True, None

    def _progress_availability(self, job: Any) -> tuple[bool, str | None]:
        try:
            self._progress_snapshot(job)
        except ObservabilitySourceError as exc:
            return False, exc.message
        return True, None

    @staticmethod
    def _availability(
        *,
        mlflow: tuple[bool, str | None],
        tensorboard: tuple[bool, str | None],
        progress: tuple[bool, str | None],
    ) -> dict[str, dict[str, bool | str | None]]:
        return {
            "mlflow": {"available": mlflow[0], "reason": mlflow[1]},
            "tensorboard": {"available": tensorboard[0], "reason": tensorboard[1]},
            "progress": {"available": progress[0], "reason": progress[1]},
            "artifacts": {"available": True, "reason": None},
        }
