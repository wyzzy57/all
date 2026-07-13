# VisioX Native Training Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the MLflow/TensorBoard iframes with a VisioX-native training dashboard that displays real training metrics, resource usage, artifacts, model graph, and parameter distributions while keeping MLflow and TensorBoard as backend-only collection stores.

**Architecture:** The training worker writes MLflow/TensorBoard data plus an atomic per-job progress snapshot into the existing shared `training-runs` volume. The API service mounts that volume read-only and exposes stable VisioX observability DTOs through a new aggregation service that tolerates partial source failure. The Vue frontend calls only VisioX APIs and renders ECharts-based views; direct links to MLflow/TensorBoard remain available only under an advanced debugging menu.

**Tech Stack:** Python 3.12, FastAPI, MLflow 3.x client, TensorBoard EventAccumulator, psutil, Ultralytics callbacks, Vue 3, TypeScript, Element Plus, Apache ECharts, Vitest, pytest, Docker Compose.

## Global Constraints

- Do not fabricate missing historical metrics; return explicit availability metadata and render an empty state.
- Preserve all existing TrainingJob, pipeline status, artifact download, inference, and evaluation behavior.
- New browser code must not call ports `5001` or `6006` during normal dashboard use.
- Do not add a database migration; time-series data stays in MLflow, TensorBoard, and the shared run directory.
- Support legacy successful job status `success` and normalized status `succeeded`.
- Limit default scalar responses to 2,000 points per series using deterministic min/max bucket downsampling.
- Load model graphs and histograms only when their corresponding frontend tab becomes active.
- Persist partial observability data when training fails or is canceled.

---

## File Map

### Backend collection and aggregation

- Create `apps/api-service/src/visiox_api/services/__init__.py`: service package marker.
- Create `apps/api-service/src/visiox_api/services/training_observability.py`: MLflow, TensorBoard, progress-snapshot readers, normalization, caching, and downsampling.
- Create `apps/api-service/src/visiox_api/routes/training_observability.py`: Pydantic DTOs and five observability endpoints.
- Modify `apps/api-service/src/visiox_api/main.py`: register the new router.
- Modify `workers/training-worker/src/visiox_training_worker/train_entrypoint.py`: emit progress, resource metrics, weight histograms, and gradient histograms.
- Modify `pyproject.toml`: add direct `psutil` dependency.
- Modify `packages/visiox-common/src/visiox_common/settings.py`: add run-root, cache, and response limit settings.
- Modify `.env.example`: document observability settings.
- Modify `infra/compose/docker-compose.yml`: mount `training-runs` read-only in API service and pass the run-root setting.

### Frontend

- Modify `apps/frontend/package.json` and lockfile: add `echarts`.
- Modify `apps/frontend/src/api/client.ts`: add native observability DTOs and client methods.
- Create `apps/frontend/src/components/training/MetricLineChart.vue`: reusable scalar/resource line chart.
- Create `apps/frontend/src/components/training/HistogramChart.vue`: native histogram renderer.
- Create `apps/frontend/src/components/training/ModelGraphChart.vue`: native model graph renderer.
- Create `apps/frontend/src/components/training/ArtifactGallery.vue`: existing artifact API-backed result gallery.
- Rewrite `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`: native dashboard orchestration, lazy loading, polling, states, and advanced links.

### Tests

- Create `tests/unit/test_training_observability_service.py`.
- Extend `tests/unit/test_training_observability.py`.
- Create `tests/integration/test_training_observability_api.py`.
- Rewrite `apps/frontend/tests/training-visualization-view.spec.ts`.
- Create `apps/frontend/tests/training-chart-components.spec.ts`.

---

### Task 1: Define the Native Observability Aggregation Contract

**Files:**
- Create: `apps/api-service/src/visiox_api/services/__init__.py`
- Create: `apps/api-service/src/visiox_api/services/training_observability.py`
- Create: `tests/unit/test_training_observability_service.py`

**Interfaces:**
- Produces: `ObservabilitySourceError(source: str, message: str)`.
- Produces: `TrainingObservabilityService(settings: Settings)`.
- Produces: `get_summary(job, pipeline, task) -> dict[str, Any]`.
- Produces: `get_scalars(job, keys, start_step, end_step, max_points) -> dict[str, list[dict[str, float]]]`.
- Produces: `get_resources(job, start_step, end_step, max_points) -> dict[str, list[dict[str, float]]]`.
- Produces: `get_graph(job) -> dict[str, Any]`.
- Produces: `get_histogram(job, kind, tag, step) -> dict[str, Any]`.
- Produces: `_downsample_points(points, max_points)` and `_normalize_metric_name(name)` as unit-testable helpers.

- [ ] **Step 1: Write failing normalization and downsampling tests**

```python
def test_normalize_ultralytics_metric_names():
    assert _normalize_metric_name("train/box_loss") == "train.box_loss"
    assert _normalize_metric_name("metrics/mAP50-95(B)") == "metrics.map50_95"
    assert _normalize_metric_name("lr/pg0") == "learning_rate"


def test_downsampling_preserves_first_last_min_and_max():
    points = [{"step": step, "value": value, "timestamp": step * 10.0} for step, value in enumerate([0, 4, 1, 8, 2, 9, 3, 7, 5, 6])]
    sampled = _downsample_points(points, max_points=6)
    assert sampled[0] == points[0]
    assert sampled[-1] == points[-1]
    assert {point["value"] for point in sampled}.issuperset({0, 9})
    assert len(sampled) <= 6
```

- [ ] **Step 2: Run the unit tests and verify RED**

Run:

```powershell
py -3.12 -m pytest tests/unit/test_training_observability_service.py -q
```

Expected: collection fails because `visiox_api.services.training_observability` does not exist.

- [ ] **Step 3: Implement metric normalization, deterministic downsampling, and source availability**

Use this stable availability shape in every response:

```python
{
    "mlflow": {"available": True, "reason": None},
    "tensorboard": {"available": False, "reason": "event file not found"},
    "progress": {"available": True, "reason": None},
    "artifacts": {"available": True, "reason": None},
}
```

The service constructor must accept injectable `mlflow_client_factory` and `event_accumulator_factory` keyword arguments so tests do not require live containers. Resolve a run using the MLflow run-name tag set to `job-{job.id}`, with the persisted `job.metrics.observability.mlflow_run_name` taking precedence. Resolve TensorBoard data from `settings.training_runs_root / "runs" / f"job-{job.id}"`.

- [ ] **Step 4: Add fake-client tests for MLflow scalar history and partial failure**

```python
def test_scalars_merge_mlflow_history_without_fabricating_missing_series(fake_job, fake_mlflow_client):
    service = TrainingObservabilityService(test_settings, mlflow_client_factory=lambda _: fake_mlflow_client)
    result = service.get_scalars(fake_job, ["train.box_loss", "metrics.map50"], None, None, 2000)
    assert [point["value"] for point in result["series"]["train.box_loss"]] == [1.4, 0.9]
    assert result["series"]["metrics.map50"] == []
    assert result["availability"]["mlflow"]["available"] is True


def test_mlflow_failure_returns_tensorboard_data_and_reason(fake_job, fake_event_accumulator):
    service = TrainingObservabilityService(
        test_settings,
        mlflow_client_factory=lambda _: (_ for _ in ()).throw(ConnectionError("offline")),
        event_accumulator_factory=lambda _: fake_event_accumulator,
    )
    result = service.get_scalars(fake_job, ["train.box_loss"], None, None, 2000)
    assert result["series"]["train.box_loss"]
    assert result["availability"]["mlflow"] == {"available": False, "reason": "offline"}
```

- [ ] **Step 5: Implement MLflow scalar history and TensorBoard scalar fallback**

MLflow is authoritative when both sources have the same normalized key. TensorBoard fills only missing series. Convert timestamps to Unix seconds and sort every series by `(step, timestamp)` before downsampling.

- [ ] **Step 6: Add event graph and histogram parser tests**

Test the stable output shapes:

```python
assert graph == {
    "nodes": [{"id": "model.0.conv", "label": "Conv", "op": "aten::_convolution", "attributes": {}}],
    "edges": [{"source": "input", "target": "model.0.conv"}],
}
assert histogram["kind"] == "weight"
assert histogram["tag"] == "weights/model.0.conv.weight"
assert histogram["step"] == 5
assert histogram["buckets"] == [{"lower": -1.0, "upper": 0.0, "count": 12.0}]
```

- [ ] **Step 7: Implement graph parsing, histogram conversion, progress snapshot reading, and mtime cache**

Cache EventAccumulator instances by `(run_path, newest_event_mtime_ns)` with a maximum of 32 entries. Parse `visiox-progress.json` only after resolving its path under the configured run root; reject path traversal by constructing the path exclusively from the validated database job ID.

- [ ] **Step 8: Run unit tests and lint**

```powershell
py -3.12 -m pytest tests/unit/test_training_observability_service.py -q
py -3.12 -m ruff check apps/api-service/src/visiox_api/services/training_observability.py tests/unit/test_training_observability_service.py
```

Expected: all tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 9: Commit**

```powershell
git add apps/api-service/src/visiox_api/services tests/unit/test_training_observability_service.py
git commit -m "feat: aggregate native training observability data"
```

---

### Task 2: Expose Stable VisioX Observability APIs

**Files:**
- Create: `apps/api-service/src/visiox_api/routes/training_observability.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Create: `tests/integration/test_training_observability_api.py`

**Interfaces:**
- Consumes: `TrainingObservabilityService` from Task 1.
- Produces: `GET /training-jobs/{id}/observability/summary`.
- Produces: `GET /training-jobs/{id}/observability/scalars?keys=...&start_step=&end_step=&max_points=`.
- Produces: `GET /training-jobs/{id}/observability/resources`.
- Produces: `GET /training-jobs/{id}/observability/graph`.
- Produces: `GET /training-jobs/{id}/observability/histograms?kind=&tag=&step=`.

- [ ] **Step 1: Write failing endpoint integration tests**

Cover these exact cases:

```python
def test_observability_summary_returns_pipeline_job_and_availability(client, seeded_training_job): ...
def test_observability_scalars_validates_max_points(client, seeded_training_job): ...
def test_observability_graph_returns_404_for_missing_job(client): ...
def test_observability_histogram_requires_weight_or_gradient_kind(client, seeded_training_job): ...
def test_observability_source_failure_remains_http_200(client, seeded_training_job): ...
```

Use FastAPI dependency override `get_training_observability_service` to inject a deterministic fake service.

- [ ] **Step 2: Run API tests and verify RED**

```powershell
py -3.12 -m pytest tests/integration/test_training_observability_api.py -q
```

Expected: route imports or endpoint requests fail because the router is not registered.

- [ ] **Step 3: Implement Pydantic DTOs and route dependencies**

Summary must include:

```json
{
  "job_id": "...",
  "pipeline_id": "...",
  "pipeline_name": "花椒检测",
  "status": "running",
  "progress": {"current_epoch": 4, "total_epochs": 40, "percent": 10.0},
  "timing": {"started_at": "...", "elapsed_seconds": 213.4, "eta_seconds": 1920.6},
  "environment": {"device": "cpu"},
  "latest_metrics": {"metrics.map50": 0.51},
  "available_scalar_keys": ["train.box_loss", "metrics.map50"],
  "available_histograms": {"weight": ["weights/model.0.conv.weight"], "gradient": []},
  "availability": {}
}
```

Use `Query(min_length=1)` for scalar keys, `Query(ge=10, le=10000)` for `max_points`, and `Literal["weight", "gradient"]` for histogram kind. A missing TrainingJob is 404; source failures remain 200 with availability reasons.

- [ ] **Step 4: Register the router and run API tests**

```powershell
py -3.12 -m pytest tests/integration/test_training_observability_api.py -q
py -3.12 -m ruff check apps/api-service/src/visiox_api/routes/training_observability.py apps/api-service/src/visiox_api/main.py
```

- [ ] **Step 5: Run existing training-job API regression tests**

```powershell
py -3.12 -m pytest tests/integration/test_training_pipeline.py -q
```

Expected: existing training, artifact, inference, and evaluation tests remain green.

- [ ] **Step 6: Commit**

```powershell
git add apps/api-service/src/visiox_api/routes/training_observability.py apps/api-service/src/visiox_api/main.py tests/integration/test_training_observability_api.py
git commit -m "feat: expose training observability api"
```

---

### Task 3: Collect Progress, Resource, Weight, and Gradient Data

**Files:**
- Modify: `workers/training-worker/src/visiox_training_worker/train_entrypoint.py`
- Modify: `tests/unit/test_training_observability.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `should_capture_epoch(epoch, total_epochs, interval) -> bool`.
- Produces: `write_progress_snapshot(trainer) -> Path`.
- Produces: `log_epoch_observability(trainer) -> None`.
- Produces: `capture_gradient_sample(trainer) -> None`.
- Progress file: `Path(trainer.save_dir) / "visiox-progress.json"` written atomically through `.tmp` plus `Path.replace()`.

- [ ] **Step 1: Write failing callback tests without importing a real YOLO model**

Use fake trainer/model/writer objects and assert:

```python
assert should_capture_epoch(1, 40, 5) is True
assert should_capture_epoch(4, 40, 5) is False
assert should_capture_epoch(5, 40, 5) is True
assert should_capture_epoch(40, 40, 5) is True
```

The progress snapshot test must assert current/total Epoch, percent, elapsed, ETA, updated time, device and latest metrics. The histogram test must prove that tensors larger than 100,000 values are sampled and that weight and gradient tags use `weights/` and `gradients/` prefixes.

- [ ] **Step 2: Run callback tests and verify RED**

```powershell
py -3.12 -m pytest tests/unit/test_training_observability.py -q
```

- [ ] **Step 3: Add `psutil>=6.0,<8.0` as a direct project dependency**

Do not rely on MLflow installing psutil transitively. Resource payload keys are fixed:

```text
system.cpu_percent
system.memory_percent
system.memory_used_gb
system.gpu_utilization_percent
system.gpu_memory_used_gb
system.gpu_memory_reserved_gb
train.images_per_second
```

- [ ] **Step 4: Implement atomic progress snapshots and resource logging**

Use `psutil.Process().memory_info().rss` and `psutil.cpu_percent(interval=None)`. For CUDA, use `torch.cuda.memory_allocated()`, `torch.cuda.memory_reserved()`, and `torch.cuda.utilization()` only when available. Omit unavailable GPU keys rather than returning zero.

- [ ] **Step 5: Implement gradient capture at `on_before_zero_grad`**

On the last training batch of capture Epochs, copy at most 100,000 values per trainable parameter into a temporary CPU dictionary on the trainer. Flush those cached samples to TensorBoard during `on_train_epoch_end`, then clear the dictionary. This avoids retaining computation graphs and avoids writing a histogram every batch.

- [ ] **Step 6: Register callbacks**

```python
model.add_callback("on_before_zero_grad", capture_gradient_sample)
model.add_callback("on_train_epoch_end", log_epoch_observability)
model.add_callback("on_train_end", write_final_progress_snapshot)
```

Keep Ultralytics MLflow and TensorBoard settings enabled. Do not remove existing `log_weight_histograms` behavior until its tests have been migrated to `log_epoch_observability`.

- [ ] **Step 7: Run tests and lint**

```powershell
py -3.12 -m pytest tests/unit/test_training_observability.py -q
py -3.12 -m ruff check workers/training-worker/src/visiox_training_worker/train_entrypoint.py tests/unit/test_training_observability.py
```

- [ ] **Step 8: Commit**

```powershell
git add pyproject.toml workers/training-worker/src/visiox_training_worker/train_entrypoint.py tests/unit/test_training_observability.py
git commit -m "feat: collect native training telemetry"
```

---

### Task 4: Wire Shared Storage and Runtime Configuration

**Files:**
- Modify: `packages/visiox-common/src/visiox_common/settings.py`
- Modify: `.env.example`
- Modify: `infra/compose/docker-compose.yml`
- Test: `tests/unit/test_training_observability_service.py`

**Interfaces:**
- Produces settings:
  - `training_runs_root: Path = Path("/workspace/training-runs")`
  - `observability_max_points: int = 2000`
  - `observability_event_cache_size: int = 32`
  - `observability_live_poll_seconds: int = 5`

- [ ] **Step 1: Add a failing settings/path-resolution test**

Verify that `TrainingObservabilityService` resolves job `abc` to `/workspace/training-runs/runs/job-abc` and that no job-provided or metrics-provided path can escape the root.

- [ ] **Step 2: Add settings and environment documentation**

Add exact `.env.example` names:

```env
VISIOX_TRAINING_RUNS_ROOT=/workspace/training-runs
VISIOX_OBSERVABILITY_MAX_POINTS=2000
VISIOX_OBSERVABILITY_EVENT_CACHE_SIZE=32
VISIOX_OBSERVABILITY_LIVE_POLL_SECONDS=5
```

- [ ] **Step 3: Mount the shared volume into API service read-only**

```yaml
api-service:
  environment:
    VISIOX_TRAINING_RUNS_ROOT: /workspace/training-runs
  volumes:
    - training-runs:/workspace/training-runs:ro
```

Do not change the worker mount from read-write.

- [ ] **Step 4: Validate Compose and service tests**

```powershell
docker compose -f infra/compose/docker-compose.yml config --quiet
py -3.12 -m pytest tests/unit/test_training_observability_service.py -q
```

- [ ] **Step 5: Rebuild only affected images and verify mounts**

```powershell
docker compose -f infra/compose/docker-compose.yml up -d --build api-service training-worker
docker exec compose-api-service-1 python -c "from visiox_common.settings import get_settings; print(get_settings().training_runs_root)"
docker exec compose-api-service-1 sh -lc "test -d /workspace/training-runs/runs"
```

- [ ] **Step 6: Commit**

```powershell
git add .env.example infra/compose/docker-compose.yml packages/visiox-common/src/visiox_common/settings.py tests/unit/test_training_observability_service.py
git commit -m "chore: wire training observability storage"
```

---

### Task 5: Add Native Frontend API Types and ECharts Components

**Files:**
- Modify: `apps/frontend/package.json`
- Modify: `apps/frontend/package-lock.json`
- Modify: `apps/frontend/src/api/client.ts`
- Create: `apps/frontend/src/components/training/MetricLineChart.vue`
- Create: `apps/frontend/src/components/training/HistogramChart.vue`
- Create: `apps/frontend/src/components/training/ModelGraphChart.vue`
- Create: `apps/frontend/tests/training-chart-components.spec.ts`

**Interfaces:**
- Produces TypeScript DTOs matching Task 2 exactly.
- Produces API methods:
  - `getTrainingObservabilitySummary(jobId)`.
  - `getTrainingObservabilityScalars(jobId, params)`.
  - `getTrainingObservabilityResources(jobId, params)`.
  - `getTrainingObservabilityGraph(jobId)`.
  - `getTrainingObservabilityHistogram(jobId, params)`.
- Produces chart props:
  - `MetricLineChart`: `{ series, unit?, height? }`.
  - `HistogramChart`: `{ histogram, height? }`.
  - `ModelGraphChart`: `{ nodes, edges, height? }`.

- [ ] **Step 1: Install ECharts**

```powershell
npm --prefix apps/frontend install echarts@^5.6.0
```

- [ ] **Step 2: Write failing component tests**

Mock `echarts/core` and assert each component initializes once, calls `setOption` with real DTO input, resizes on window resize, updates when props change, and disposes on unmount. Assert the graph uses `type: "graph"`, `roam: true`, and a force layout; assert histograms use numeric x-axis buckets rather than categorical fake labels.

- [ ] **Step 3: Run component tests and verify RED**

```powershell
npm --prefix apps/frontend test -- --run tests/training-chart-components.spec.ts
```

- [ ] **Step 4: Implement typed API client methods**

Encode repeated scalar keys as a comma-separated `keys` query value. Do not expose MLflow or TensorBoard URLs in any public DTO.

- [ ] **Step 5: Implement focused chart components**

Import only required ECharts modules through `echarts/core`: CanvasRenderer, LineChart, BarChart, GraphChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent and ToolboxComponent. Every component must have a stable min-height so loading and empty states do not shift the layout.

- [ ] **Step 6: Run tests, typecheck, and build**

```powershell
npm --prefix apps/frontend test -- --run tests/training-chart-components.spec.ts
npm --prefix apps/frontend run typecheck
npm --prefix apps/frontend run build
```

- [ ] **Step 7: Commit**

```powershell
git add apps/frontend/package.json apps/frontend/package-lock.json apps/frontend/src/api/client.ts apps/frontend/src/components/training apps/frontend/tests/training-chart-components.spec.ts
git commit -m "feat: add native training chart components"
```

---

### Task 6: Build the Native Overview, Metrics, and Resource Dashboard

**Files:**
- Rewrite: `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`
- Rewrite: `apps/frontend/tests/training-visualization-view.spec.ts`

**Interfaces:**
- Consumes Task 5 API methods and `MetricLineChart`.
- Produces native tabs: `overview`, `metrics`, `resources`, `analysis`, `graph`, `histograms`.
- Polling rule: every 5 seconds only for `queued` or `running`; stop immediately for `success`, `succeeded`, `failed`, or `canceled`.

- [ ] **Step 1: Replace iframe expectations with failing native-dashboard tests**

The tests must assert:

```typescript
expect(wrapper.find("iframe").exists()).toBe(false);
expect(wrapper.text()).toContain("当前 Epoch");
expect(wrapper.text()).toContain("mAP50-95");
expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledWith("job-1");
```

Also test selecting another job cancels stale requests, unavailable MLflow does not hide TensorBoard-backed curves, and terminal status stops fake-timer polling.

- [ ] **Step 2: Run the view test and verify RED**

```powershell
npm --prefix apps/frontend test -- --run tests/training-visualization-view.spec.ts
```

Expected: assertions fail because the view still renders MLflow/TensorBoard iframes.

- [ ] **Step 3: Implement the native page shell and overview**

Keep the real training list on the left. The right overview displays status, progress bar, current/total Epoch, elapsed, ETA, device, batch, image size, learning rate, latest precision/recall/mAP and source availability. Do not put sections inside decorative cards; use full-width bands and compact metric cells.

- [ ] **Step 4: Implement metric and resource tabs with lazy requests**

Metric tab requests only the available keys in the summary. Resource tab calls only the resource endpoint. Preserve zoom/legend state during polling by updating chart series rather than recreating components.

- [ ] **Step 5: Add request generation guards and polling cleanup**

Increment a selection generation counter on every job switch. Ignore responses whose generation no longer matches. Clear timers on selection change and component unmount.

- [ ] **Step 6: Run view and chart tests**

```powershell
npm --prefix apps/frontend test -- --run tests/training-visualization-view.spec.ts tests/training-chart-components.spec.ts
npm --prefix apps/frontend run typecheck
```

- [ ] **Step 7: Commit**

```powershell
git add apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue apps/frontend/tests/training-visualization-view.spec.ts
git commit -m "feat: build native training dashboard"
```

---

### Task 7: Add Artifact Analysis, Model Graph, and Histogram Views

**Files:**
- Create: `apps/frontend/src/components/training/ArtifactGallery.vue`
- Modify: `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`
- Modify: `apps/frontend/tests/training-visualization-view.spec.ts`

**Interfaces:**
- Consumes existing `api.listTrainingJobArtifacts` and `api.trainingJobArtifactDownloadUrl`.
- Consumes native graph and histogram APIs from Task 5.
- Advanced debugging links use `VITE_MLFLOW_URL` and `VITE_TENSORBOARD_URL`, but no iframe is permitted.

- [ ] **Step 1: Write failing lazy-load and empty-state tests**

Assert graph API is not called before the graph tab opens, histogram API is not called before tag/step selection, analysis shows real `results.png`/`confusion_matrix.png` items, and missing graph/gradient data renders `该训练未记录计算图` or `该训练未记录梯度分布`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
npm --prefix apps/frontend test -- --run tests/training-visualization-view.spec.ts
```

- [ ] **Step 3: Implement artifact gallery**

Group existing artifacts into `训练结果`, `评估曲线`, `混淆矩阵`, and `训练批次`. Use `object-fit: contain`, preserve source aspect ratios, and keep download links backed by the existing API.

- [ ] **Step 4: Implement graph and histogram tabs**

Graph view shows node details in a side inspector after selection. Histogram view has segmented `权重/梯度`, a parameter tag menu, and an Epoch step menu. A selection change fetches exactly one histogram payload.

- [ ] **Step 5: Move raw tools under advanced debugging**

Add an overflow menu with `打开 MLflow` and `打开 TensorBoard`. These links open new windows; remove all iframe code, iframe styles, and default tool state.

- [ ] **Step 6: Run frontend regression suite and build**

```powershell
npm --prefix apps/frontend test -- --run tests/training-visualization-view.spec.ts tests/training-chart-components.spec.ts tests/model-space-view.spec.ts
npm --prefix apps/frontend run build
```

- [ ] **Step 7: Commit**

```powershell
git add apps/frontend/src/components/training/ArtifactGallery.vue apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue apps/frontend/tests/training-visualization-view.spec.ts
git commit -m "feat: add native training analysis views"
```

---

### Task 8: Verify a Real Training Run End to End

**Files:**
- Create: `docs/runbooks/native-training-visualization.md`

**Interfaces:**
- Validates the complete data flow from Ultralytics callbacks to VisioX UI.
- Produces reproducible operator commands and troubleshooting checks.

- [ ] **Step 1: Run the complete automated test set**

Use the project Python 3.12 environment, not the host Python 3.10 executable:

```powershell
docker compose -f infra/compose/docker-compose.yml run --rm api-service python -m pytest tests/unit/test_training_observability.py tests/unit/test_training_observability_service.py tests/integration/test_training_observability_api.py tests/integration/test_training_pipeline.py -q
npm --prefix apps/frontend test -- --run tests/training-visualization-view.spec.ts tests/training-chart-components.spec.ts tests/model-space-view.spec.ts
npm --prefix apps/frontend run build
docker compose -f infra/compose/docker-compose.yml config --quiet
```

- [ ] **Step 2: Start observability services and verify health**

```powershell
docker compose -f infra/compose/docker-compose.yml up -d api-service training-worker mlflow tensorboard
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5001/
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:6006/
```

- [ ] **Step 3: Submit one short real CPU training run**

Use a validated detection dataset and YOLO26n with `epochs=2`, `batch=1`, `imgsz=320`, `device=cpu`, and a distinct pipeline name `observability-smoke`. Do not reuse an existing successful job because old event files may not contain resource or gradient data.

```powershell
$baseModel = (Invoke-RestMethod "http://127.0.0.1:8000/base-models?task=detect&limit=200").items |
  Where-Object { $_.filename -eq "yolo26n.pt" -and $_.status -eq "ready" } |
  Select-Object -First 1
$dataset = (Invoke-RestMethod "http://127.0.0.1:8000/datasets?task=detect&status=validated&limit=200").items |
  Select-Object -First 1
if (-not $baseModel) { throw "No ready yolo26n.pt base model" }
if (-not $dataset) { throw "No validated detection dataset" }
$pipelineBody = @{
  name = "observability-smoke-$((Get-Date).ToString('yyyyMMdd-HHmmss'))"
  task = "detect"
  scale = "n"
  base_model_id = $baseModel.id
  dataset_id = $dataset.id
  params_template = @{ epochs = 2; batch = 1; imgsz = 320 }
  default_environment = @{ device = "cpu" }
} | ConvertTo-Json -Depth 6
$pipeline = Invoke-RestMethod -Method Post -ContentType "application/json" -Body $pipelineBody http://127.0.0.1:8000/pipelines
$jobBody = @{ params = @{}; environment = @{ device = "cpu" } } | ConvertTo-Json -Depth 4
$job = Invoke-RestMethod -Method Post -ContentType "application/json" -Body $jobBody "http://127.0.0.1:8000/pipelines/$($pipeline.id)/jobs"
$jobId = $job.id
```

- [ ] **Step 4: Verify native API payloads contain real data**

For the returned job ID, verify:

```powershell
$summary = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/summary"
$scalars = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/scalars?keys=train.box_loss,metrics.map50&max_points=2000"
$resources = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/resources"
$graph = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/graph"
$weightTag = $summary.available_histograms.weight | Select-Object -First 1
$encodedTag = [uri]::EscapeDataString($weightTag)
$histogram = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/histograms?kind=weight&tag=$encodedTag&step=1"
```

Acceptance: scalar series contain at least two real Epoch points, progress reaches 100%, resource series contains CPU and memory, at least one weight histogram exists, and graph availability accurately reflects whether Ultralytics emitted a graph.

- [ ] **Step 5: Browser-verify the VisioX native page**

At desktop `1440x900` and mobile `390x844`, verify:

- no iframe exists;
- chart canvases contain nonblank pixels;
- job selection updates all visible identifiers;
- tabs do not overlap or resize unexpectedly;
- result images preserve their aspect ratio;
- source failure and historical-data empty states are readable;
- graph supports zoom/pan when available;
- histogram tag and Epoch selectors work.

- [ ] **Step 6: Write the runbook**

Document service URLs, container names, API checks, expected event directory, cache behavior, new-vs-old training data behavior, and commands for diagnosing missing MLflow/TensorBoard data. Do not describe MLflow/TensorBoard as user-facing VisioX pages.

- [ ] **Step 7: Final regression and diff checks**

```powershell
py -3.12 -m ruff check apps/api-service/src/visiox_api workers/training-worker/src/visiox_training_worker tests/unit tests/integration
npm --prefix apps/frontend run typecheck
git diff --check
docker compose -f infra/compose/docker-compose.yml ps
```

- [ ] **Step 8: Commit**

```powershell
git add docs/runbooks/native-training-visualization.md
git commit -m "docs: add native training visualization runbook"
```

---

## Completion Checklist

- [ ] VisioX normal dashboard makes zero direct requests to MLflow/TensorBoard ports.
- [ ] Every observability endpoint returns stable DTOs and partial availability instead of failing wholesale.
- [ ] A new real training run shows scalar metrics, resource usage, result artifacts, and weight histograms.
- [ ] Gradient histograms appear when Ultralytics exposes gradients at `on_before_zero_grad`.
- [ ] Model graph is shown when emitted and has an honest empty state otherwise.
- [ ] Old jobs remain selectable without fabricated data.
- [ ] Training success, failure, and cancellation behavior is unchanged.
- [ ] All Python, frontend, Compose, and browser verification steps pass.
