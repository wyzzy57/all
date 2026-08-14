from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from pathlib import Path
from threading import Lock
from typing import Any
import json
import math
import re
from urllib.parse import quote

from visiox_common.settings import Settings
from visiox_api.services.llm_training_analysis import analyze_llm_training


_DEFAULT_MAX_POINTS = 2_000
_OBSERVABILITY_MAX_FILE_BYTES = 8 * 1024 * 1024
_OBSERVABILITY_MAX_LINES = 10_000
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
_OBSERVABILITY_ADAPTERS: dict[str, type[Any]] | None = None


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

    def for_engine(self, engine: str) -> Any:
        global _OBSERVABILITY_ADAPTERS
        from visiox_api.services.observability.llamafactory import (
            LlamaFactoryObservabilityAdapter,
        )
        from visiox_api.services.observability.paddlex import (
            PaddleXObservabilityAdapter,
        )
        from visiox_api.services.observability.ultralytics import (
            UltralyticsObservabilityAdapter,
        )

        if _OBSERVABILITY_ADAPTERS is None:
            _OBSERVABILITY_ADAPTERS = {
                "yolo26": UltralyticsObservabilityAdapter,
                "ultralytics": UltralyticsObservabilityAdapter,
                "llamafactory": LlamaFactoryObservabilityAdapter,
                "paddlex": PaddleXObservabilityAdapter,
            }
        adapter_type = _OBSERVABILITY_ADAPTERS.get(engine)
        if adapter_type is None:
            raise ValueError(f"Unknown observability framework {engine!r}")
        return adapter_type(self)

    def for_pipeline(self, pipeline: Any) -> Any:
        from visiox_api.services.framework_adapters import FrameworkAdapterCatalog
        from visiox_training.errors import FrameworkAdapterError
        from visiox_training.registry import AdapterRegistry

        framework = getattr(pipeline, "framework", None)
        adapter_key = getattr(pipeline, "adapter_key", None)
        adapter_version = getattr(pipeline, "adapter_version", None)
        if adapter_key:
            catalog = FrameworkAdapterCatalog(self.settings)
            try:
                registered = (
                    catalog.registry.get(adapter_key, str(adapter_version))
                    if adapter_version
                    else next(
                        (
                            adapter
                            for adapter in catalog.registry.list()
                            if adapter.adapter_key == adapter_key
                        ),
                        None,
                    )
                )
            except FrameworkAdapterError as exc:
                raise ValueError(str(exc)) from exc
            if registered is None:
                raise ValueError(f"Unknown observability adapter {adapter_key!r}")
            if framework and registered.framework != framework:
                raise ValueError(
                    f"Adapter {adapter_key!r} does not belong to framework {framework!r}"
                )
            framework = framework or registered.framework
        if not framework:
            engine = str(getattr(pipeline, "engine", ""))
            try:
                framework = AdapterRegistry.legacy_engine_mapping(engine).framework
            except Exception:
                framework = engine
        return self.for_engine(str(framework))

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

        snapshot_metrics = snapshot.get("latest_metrics")
        if isinstance(snapshot_metrics, dict):
            for name, value in snapshot_metrics.items():
                if isinstance(value, int | float):
                    latest_metrics[str(name)] = float(value)
                    scalar_keys.add(_normalize_metric_name(str(name)))
        if getattr(pipeline, "engine", "yolo26") == "llamafactory":
            metric_samples = self._jsonl_samples(job, "visiox-metrics.jsonl")
            if metric_samples:
                for name, value in metric_samples[-1].items():
                    if name in {"step", "timestamp"} or not isinstance(value, int | float):
                        continue
                    latest_metrics[str(name)] = float(value)
                for sample in metric_samples:
                    for name, value in sample.items():
                        if name not in {"step", "timestamp"} and isinstance(value, int | float):
                            scalar_keys.add(_normalize_metric_name(str(name)))
        if getattr(pipeline, "engine", "yolo26") == "paddlex":
            metric_samples = self._jsonl_samples(job, "visiox-metrics.jsonl")
            for sample in metric_samples:
                metrics = sample.get("metrics")
                if not isinstance(metrics, dict):
                    continue
                for name, value in metrics.items():
                    if not isinstance(value, int | float) or isinstance(value, bool):
                        continue
                    latest_metrics[str(name)] = float(value)
                    scalar_keys.add(
                        self.for_engine("paddlex")
                        .metric_identity(str(name))
                        .canonical_name
                    )
        return {
            "job_id": str(job.id),
            "engine": str(
                getattr(pipeline, "framework", None)
                or getattr(pipeline, "engine", "yolo26")
            ),
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
            "secondary_actions": self._secondary_actions(),
            "availability": self._availability(
                mlflow=mlflow_availability,
                tensorboard=tensorboard_availability,
                progress=progress_availability,
                resources=self._resources_availability(job, snapshot),
                logs=self._logs_availability(job),
                artifacts=self._artifacts_availability(job),
            ),
        }

    def get_scalars(
        self,
        job: Any,
        keys: list[str],
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
        *,
        engine: str = "yolo26",
    ) -> dict[str, Any]:
        normalized_keys = list(dict.fromkeys(_normalize_metric_name(key) for key in keys))
        series = {key: [] for key in normalized_keys}
        mlflow_available = True
        mlflow_reason: str | None = None
        try:
            for key, points in self._mlflow_scalars(
                job, normalized_keys, engine=engine
            ).items():
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
                identity = self.for_engine(engine).metric_identity(str(tag))
                result_key = next(
                    (
                        key
                        for key in (identity.canonical_name, normalized_tag)
                        if key in series
                    ),
                    None,
                )
                if result_key is None or series[result_key]:
                    continue
                series[result_key] = self._scalar_points(
                    accumulator.Scalars(tag),
                    start_step,
                    end_step,
                    raw_name=str(tag),
                    engine=engine,
                    source="tensorboard",
                )
        except ObservabilitySourceError as exc:
            tensorboard_available = False
            tensorboard_reason = exc.message
        except Exception as exc:
            tensorboard_available = False
            tensorboard_reason = str(exc)

        if engine == "llamafactory":
            jsonl_keys = {key for key, points in series.items() if not points}
            for sample in self._jsonl_samples(job, "visiox-metrics.jsonl"):
                step = sample.get("step")
                timestamp = sample.get("timestamp")
                if not isinstance(step, int | float) or not isinstance(timestamp, int | float):
                    continue
                for name, value in sample.items():
                    normalized = _normalize_metric_name(str(name))
                    identity = self.for_engine(engine).metric_identity(str(name))
                    result_key = next(
                        (
                            key
                            for key in (identity.canonical_name, normalized)
                            if key in jsonl_keys
                        ),
                        None,
                    )
                    if result_key is not None and isinstance(value, int | float):
                        series[result_key].append(
                            self._scalar_point(
                                identity,
                                step=step,
                                epoch=sample.get("epoch"),
                                value=value,
                                timestamp=timestamp,
                                source="jsonl",
                            )
                        )

        if engine == "paddlex":
            self._fill_paddlex_fallbacks(job, series)

        progress_keys = {key for key, points in series.items() if not points}
        if progress_keys:
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
                            identity = self.for_engine(engine).metric_identity(str(name))
                            result_key = next(
                                (
                                    key
                                    for key in (identity.canonical_name, normalized)
                                    if key in progress_keys
                                ),
                                None,
                            )
                            if result_key is not None and isinstance(value, int | float):
                                series[result_key].append(
                                    self._scalar_point(
                                        identity,
                                        step=step,
                                        epoch=sample.get("epoch"),
                                        value=value,
                                        timestamp=timestamp,
                                        source="progress",
                                    )
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
                job=job,
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
        *,
        engine: str = "yolo26",
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
                    identity = self._resource_metric_identity(str(name))
                    series.setdefault(name, []).append(
                        self._scalar_point(
                            identity,
                            step=step,
                            epoch=sample.get("epoch"),
                            value=value,
                            timestamp=timestamp,
                            source="progress",
                        )
                    )
        except ObservabilitySourceError as exc:
            progress_available = False
            progress_reason = exc.message

        if engine == "llamafactory":
            jsonl_series = self._resource_series_from_samples(
                self._jsonl_samples(job, "resource_metrics.jsonl")
            )
            for name, points in jsonl_series.items():
                if points:
                    series[name] = points

        limit = self._point_limit(max_points)
        for name, points in series.items():
            series[name] = _downsample_points(self._filter_points(points, start_step, end_step), limit)
        return {
            "series": series,
            "availability": self._availability(
                job=job,
                mlflow=self._mlflow_availability(job),
                tensorboard=self._event_availability(job),
                progress=(progress_available, progress_reason),
            ),
        }

    def get_analysis(self, job: Any, *, engine: str = "yolo26") -> dict[str, Any]:
        if engine != "llamafactory":
            return {
                "findings": [],
                "availability": self._availability(
                    job=job,
                    mlflow=self._mlflow_availability(job),
                    tensorboard=self._event_availability(job),
                    progress=self._progress_availability(job),
                ),
            }
        metric_samples = self._jsonl_samples(job, "visiox-metrics.jsonl")
        resource_samples = self._jsonl_samples(job, "resource_metrics.jsonl")
        scalar_names = {
            _normalize_metric_name(str(name))
            for sample in metric_samples
            for name, value in sample.items()
            if name not in {"step", "timestamp"} and isinstance(value, int | float)
        }
        scalar_series = {
            name: self._series_from_samples(metric_samples, name)
            for name in scalar_names
        }
        resource_series = self._resource_series_from_samples(resource_samples)
        return {
            "findings": analyze_llm_training(scalar_series, resource_series),
            "availability": self._availability(
                job=job,
                mlflow=self._mlflow_availability(job),
                tensorboard=self._event_availability(job),
                progress=self._progress_availability(job),
            ),
        }

    def get_artifacts(self, job: Any) -> dict[str, Any]:
        path = self._run_path(job) / "artifact-manifest.json"
        artifacts: list[dict[str, Any]] = []
        reason: str | None = None
        try:
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
            else:
                metrics = getattr(job, "metrics", {})
                payload = {
                    "artifacts": [
                        {
                            "path": name,
                            "artifact_type": "training_output",
                            # Remote workers currently report artifact URIs before
                            # the optional local manifest is materialized.
                            "size_bytes": 0,
                            "sha256": "",
                        }
                        for name in (
                            list((metrics.get("weights") or {}).keys())
                            if isinstance(metrics, dict)
                            and isinstance(metrics.get("weights"), dict)
                            else []
                        )
                    ]
                }
                if not payload["artifacts"]:
                    raise FileNotFoundError("training artifact manifest not found")
            values = payload.get("artifacts") if isinstance(payload, dict) else None
            if not isinstance(values, list):
                raise ValueError("artifact manifest is invalid")
            metrics = getattr(job, "metrics", {})
            available_names = set()
            if isinstance(metrics, dict):
                artifact_uris = metrics.get("artifacts")
                if isinstance(artifact_uris, dict):
                    available_names.update(str(name) for name in artifact_uris)
                weight_uris = metrics.get("weights")
                if isinstance(weight_uris, dict):
                    available_names.update(str(name) for name in weight_uris)
                if isinstance(metrics.get("adapter"), str):
                    available_names.add("adapter_model.safetensors")
            artifacts = [
                {
                    **item,
                    "download_url": (
                        f"/training-jobs/{job.id}/observability/artifacts/"
                        f"{quote(str(item.get('path', '')), safe='')}"
                        + (
                            f"?attempt_id={quote(str(getattr(job, 'attempt_id')), safe='')}"
                            if getattr(job, "attempt_id", None)
                            else ""
                        )
                    ),
                }
                for item in values
                if isinstance(item, dict)
                and str(item.get("path", "")).rsplit("/", 1)[-1] in available_names
            ]
        except (OSError, ValueError) as exc:
            reason = str(exc)
        return {
            "items": artifacts,
            "availability": self._availability(
                job=job,
                mlflow=self._mlflow_availability(job),
                tensorboard=self._event_availability(job),
                progress=self._progress_availability(job),
                artifacts=(reason is None, reason),
            ),
        }

    def _mlflow_scalars(
        self, job: Any, keys: list[str], *, engine: str
    ) -> dict[str, list[dict[str, Any]]]:
        client, runs = self._mlflow_client_and_runs(job)
        if not runs:
            return {key: [] for key in keys}

        run = runs[0]
        run_id = str(run.info.run_id)
        metric_names = self._mlflow_metric_names(run, keys, engine=engine)
        result = {key: [] for key in keys}
        for normalized_key, names in metric_names.items():
            for name in names:
                try:
                    history = client.get_metric_history(run_id, name)
                except Exception as exc:
                    raise ObservabilitySourceError("mlflow", str(exc)) from exc
                identity = self.for_engine(engine).metric_identity(name)
                points = [
                    {
                        "canonical_name": identity.canonical_name,
                        "raw_name": identity.raw_name,
                        "unit": identity.unit,
                        "split": identity.split,
                        "step": float(item.step),
                        "epoch": None,
                        "value": float(item.value),
                        "timestamp": float(item.timestamp) / 1_000,
                        "source": "mlflow",
                    }
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
        event_files = list(run_path.rglob("events.out.tfevents.*"))
        if not event_files:
            metrics = getattr(job, "metrics", {})
            event_uri = (
                metrics.get("observability", {}).get("tensorboard_event_uri")
                if isinstance(metrics, dict)
                and isinstance(metrics.get("observability"), dict)
                else None
            )
            if isinstance(event_uri, str):
                raise ObservabilitySourceError(
                    "tensorboard",
                    "TensorBoard event is stored remotely; open TensorBoard to view it",
                )
        if not event_files:
            raise ObservabilitySourceError("tensorboard", "event file not found")
        newest = max(event_files, key=lambda path: path.stat().st_mtime_ns)
        event_path = newest.parent
        newest_mtime = newest.stat().st_mtime_ns
        key = (event_path, newest_mtime)
        with self._event_accumulator_lock:
            accumulator = self._event_accumulators.get(key)
            if accumulator is None:
                try:
                    accumulator = self._event_accumulator_factory(str(event_path))
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

    def _jsonl_samples(self, job: Any, filename: str) -> list[dict[str, Any]]:
        path = self._run_path(job) / filename
        if not path.is_file():
            return []
        samples: list[dict[str, Any]] = []
        try:
            for line in self._bounded_text_lines(path):
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if isinstance(payload, dict):
                    samples.append(payload)
        except OSError:
            return []
        return samples

    @staticmethod
    def _bounded_text_lines(path: Path) -> list[str]:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            length = min(size, _OBSERVABILITY_MAX_FILE_BYTES)
            stream.seek(-length, 2)
            payload = stream.read(length)
        if size > length:
            _, _, payload = payload.partition(b"\n")
        return payload.decode("utf-8", errors="replace").splitlines()[
            -_OBSERVABILITY_MAX_LINES:
        ]

    @staticmethod
    def _series_from_samples(samples: list[dict[str, Any]], name: str) -> list[dict[str, float]]:
        points: list[dict[str, float]] = []
        for sample in samples:
            step = sample.get("step")
            timestamp = sample.get("timestamp")
            value = sample.get(name)
            if all(isinstance(item, int | float) and not isinstance(item, bool) for item in (step, timestamp, value)):
                points.append({"step": float(step), "timestamp": float(timestamp), "value": float(value)})
        return points

    @classmethod
    def _resource_series_from_samples(
        cls, samples: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        series: dict[str, list[dict[str, Any]]] = {}
        for sample in samples:
            step = sample.get("step")
            timestamp = sample.get("timestamp")
            if not isinstance(step, int | float) or not isinstance(timestamp, int | float):
                continue
            for name, value in sample.items():
                if name in {"step", "timestamp", "gpus"} or not isinstance(value, int | float):
                    continue
                series.setdefault(str(name), []).append(
                    cls._scalar_point(
                        cls._resource_metric_identity(str(name)),
                        step=step,
                        epoch=sample.get("epoch"),
                        timestamp=timestamp,
                        value=value,
                        source="resources",
                    )
                )
            gpus = sample.get("gpus")
            if not isinstance(gpus, list):
                continue
            for gpu in gpus:
                if not isinstance(gpu, dict):
                    continue
                identifier = gpu.get("uuid") or gpu.get("index")
                if identifier is None:
                    continue
                for name, value in gpu.items():
                    if name in {"uuid", "index"} or not isinstance(value, int | float):
                        continue
                    key = f"gpu.{identifier}.{name}"
                    series.setdefault(key, []).append(
                        cls._scalar_point(
                            cls._resource_metric_identity(key),
                            step=step,
                            epoch=sample.get("epoch"),
                            timestamp=timestamp,
                            value=value,
                            source="resources",
                        )
                    )
        return series

    def _run_path(self, job: Any) -> Path:
        job_id = str(getattr(job, "id", ""))
        if not _SAFE_JOB_ID.fullmatch(job_id):
            raise ObservabilitySourceError("progress", "invalid training job id")
        root = Path(getattr(self.settings, "training_runs_root", "/workspace/training-runs")).resolve()
        run_path = root / "runs" / f"job-{job_id}"
        attempt_number = getattr(job, "attempt_number", None)
        if attempt_number is not None:
            if not isinstance(attempt_number, int) or isinstance(attempt_number, bool) or attempt_number < 1:
                raise ObservabilitySourceError("progress", "invalid training attempt number")
            run_path = run_path / f"attempt-{attempt_number}"
        run_path = run_path.resolve()
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

        return EventAccumulator(run_path, size_guidance={"scalars": 0})

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

    def _scalar_points(
        self,
        events: Iterable[Any],
        start_step: int | None,
        end_step: int | None,
        *,
        raw_name: str,
        engine: str,
        source: str,
    ) -> list[dict[str, Any]]:
        identity = self.for_engine(engine).metric_identity(raw_name)
        points = [
            {
                "canonical_name": identity.canonical_name,
                "raw_name": identity.raw_name,
                "unit": identity.unit,
                "split": identity.split,
                "step": float(event.step),
                "epoch": None,
                "value": float(event.value),
                "timestamp": float(event.wall_time),
                "source": source,
            }
            for event in events
        ]
        return TrainingObservabilityService._filter_points(points, start_step, end_step)

    @staticmethod
    def _mlflow_run_name(job: Any) -> str:
        attempt_number = getattr(job, "attempt_number", None)
        if isinstance(attempt_number, int) and not isinstance(attempt_number, bool) and attempt_number > 0:
            return f"visiox-{job.id}-attempt-{attempt_number}"
        metrics = getattr(job, "metrics", {})
        if isinstance(metrics, dict):
            observability = metrics.get("observability")
            if isinstance(observability, dict) and isinstance(observability.get("mlflow_run_name"), str):
                return observability["mlflow_run_name"]
        return f"job-{job.id}"

    def _mlflow_metric_names(
        self, run: Any, keys: list[str], *, engine: str
    ) -> dict[str, list[str]]:
        names = {key: [key, key.replace(".", "/", 1)] for key in keys}
        metrics = getattr(getattr(run, "data", None), "metrics", {})
        if not isinstance(metrics, dict):
            return names
        for name in metrics:
            normalized = _normalize_metric_name(str(name))
            identity = self.for_engine(engine).metric_identity(str(name))
            result_key = next(
                (
                    key
                    for key in (identity.canonical_name, normalized)
                    if key in names
                ),
                None,
            )
            if result_key is not None and name not in names[result_key]:
                names[result_key].insert(0, str(name))
        return names

    def _fill_paddlex_fallbacks(
        self, job: Any, series: dict[str, list[dict[str, Any]]]
    ) -> None:
        samples = self._jsonl_samples(job, "visiox-metrics.jsonl")
        self._fill_metric_samples(series, samples, source="jsonl")
        if all(series.values()):
            return
        from visiox_api.services.observability.paddlex import parse_log_line

        path = self._run_path(job) / "stdout.log"
        if not path.is_file():
            return
        try:
            log_samples: list[dict[str, Any]] = []
            current_step = 0
            current_epoch: float | None = None
            for index, line in enumerate(self._bounded_text_lines(path), start=1):
                sample = parse_log_line(line)
                if not sample.get("metrics"):
                    continue
                if isinstance(sample.get("step"), int | float):
                    current_step = int(sample["step"])
                elif current_step == 0:
                    current_step = index
                if isinstance(sample.get("epoch"), int | float):
                    current_epoch = float(sample["epoch"])
                sample.setdefault("step", current_step)
                if current_epoch is not None:
                    sample.setdefault("epoch", current_epoch)
                log_samples.append(sample)
        except OSError:
            return
        timestamp = path.stat().st_mtime
        for sample in log_samples:
            sample.setdefault("timestamp", timestamp)
        self._fill_metric_samples(series, log_samples, source="logs")

    def _fill_metric_samples(
        self,
        series: dict[str, list[dict[str, Any]]],
        samples: list[dict[str, Any]],
        *,
        source: str,
    ) -> None:
        missing = {key for key, points in series.items() if not points}
        for sample in samples:
            metrics = sample.get("metrics")
            if not isinstance(metrics, dict):
                continue
            step = sample.get("step")
            timestamp = sample.get("timestamp")
            if not isinstance(step, int | float) or not isinstance(
                timestamp, int | float
            ):
                continue
            for raw_name, value in metrics.items():
                if not isinstance(value, int | float) or isinstance(value, bool):
                    continue
                identity = self.for_engine("paddlex").metric_identity(str(raw_name))
                key = next(
                    (
                        candidate
                        for candidate in (
                            identity.canonical_name,
                            _normalize_metric_name(str(raw_name)),
                        )
                        if candidate in missing
                    ),
                    None,
                )
                if key is None:
                    continue
                series[key].append(
                    self._scalar_point(
                        identity,
                        step=step,
                        epoch=sample.get("epoch"),
                        value=value,
                        timestamp=timestamp,
                        source=source,
                    )
                )

    @staticmethod
    def _scalar_point(
        identity: Any,
        *,
        step: Any,
        epoch: Any,
        value: Any,
        timestamp: Any,
        source: str,
    ) -> dict[str, Any]:
        return {
            "canonical_name": identity.canonical_name,
            "raw_name": identity.raw_name,
            "unit": identity.unit,
            "split": identity.split,
            "step": float(step),
            "epoch": float(epoch) if isinstance(epoch, int | float) else None,
            "value": float(value),
            "timestamp": float(timestamp),
            "source": source,
        }

    @staticmethod
    def _resource_metric_identity(raw_name: str) -> Any:
        from visiox_api.services.observability.base import MetricIdentity

        canonical_name, unit = {
            "system.cpu_percent": ("system.cpu.utilization", "percent"),
            "cpu_percent": ("system.cpu.utilization", "percent"),
            "system.memory_percent": ("system.memory.utilization", "percent"),
            "system.memory_used_gb": ("system.memory.used", "gigabyte"),
        }.get(raw_name, (raw_name, "scalar"))
        if raw_name.startswith("gpu.") and raw_name.endswith(
            "utilization_percent"
        ):
            canonical_name, unit = "system.gpu.utilization", "percent"
        elif raw_name.startswith("gpu.") and "memory_used" in raw_name:
            canonical_name = "system.gpu.memory_used"
            unit = "megabyte" if raw_name.endswith("_mb") else "gigabyte"
        return MetricIdentity(canonical_name, raw_name, unit, None)

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

    def _resources_availability(
        self, job: Any, snapshot: dict[str, Any] | None = None
    ) -> tuple[bool, str | None]:
        snapshot = snapshot or {}
        if "resources" in snapshot or "resource_metrics" in snapshot:
            return True, None
        if (self._run_path(job) / "resource_metrics.jsonl").is_file():
            return True, None
        return False, "resource metrics not found"

    def _logs_availability(self, job: Any) -> tuple[bool, str | None]:
        run_path = self._run_path(job)
        available = any(
            (run_path / name).is_file()
            for name in ("stdout.log", "stderr.log", "train.log", "training.log")
        )
        if not available:
            metrics = getattr(job, "metrics", {})
            observability = metrics.get("observability") if isinstance(metrics, dict) else None
            if isinstance(observability, dict) and observability.get("log_stream_id"):
                return True, None
        return available, None if available else "training logs not found"

    def _artifacts_availability(self, job: Any) -> tuple[bool, str | None]:
        available = (self._run_path(job) / "artifact-manifest.json").is_file()
        if not available:
            metrics = getattr(job, "metrics", {})
            available = isinstance(metrics, dict) and any(
                key in metrics for key in ("artifacts", "weights", "adapter")
            )
        return available, None if available else "artifact manifest not found"

    def _secondary_actions(self) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        for source, setting in (
            ("mlflow", "mlflow_public_url"),
            ("tensorboard", "tensorboard_public_url"),
        ):
            url = getattr(self.settings, setting, None)
            if isinstance(url, str) and url:
                actions.append({"source": source, "url": url})
        return actions

    def _availability(
        self,
        *,
        job: Any | None = None,
        mlflow: tuple[bool, str | None],
        tensorboard: tuple[bool, str | None],
        progress: tuple[bool, str | None],
        resources: tuple[bool, str | None] | None = None,
        logs: tuple[bool, str | None] | None = None,
        artifacts: tuple[bool, str | None] | None = None,
    ) -> dict[str, dict[str, bool | str | None]]:
        snapshot: dict[str, Any] = {}
        if job is not None and resources is None:
            try:
                snapshot = self._progress_snapshot(job)
            except ObservabilitySourceError:
                pass
        resources = resources or (
            self._resources_availability(job, snapshot)
            if job is not None
            else (False, "not reported")
        )
        logs = logs or (
            self._logs_availability(job)
            if job is not None
            else (False, "not reported")
        )
        artifacts = artifacts or (
            self._artifacts_availability(job)
            if job is not None
            else (False, "not reported")
        )
        return {
            "mlflow": {"available": mlflow[0], "reason": mlflow[1]},
            "tensorboard": {"available": tensorboard[0], "reason": tensorboard[1]},
            "progress": {"available": progress[0], "reason": progress[1]},
            "resources": {"available": resources[0], "reason": resources[1]},
            "logs": {"available": logs[0], "reason": logs[1]},
            "artifacts": {"available": artifacts[0], "reason": artifacts[1]},
        }
