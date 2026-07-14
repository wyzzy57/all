# Native Training Visualization Runbook

VisioX exposes training telemetry through its own API and UI. MLflow and
TensorBoard are collection and troubleshooting backends; they are not normal
user-facing VisioX pages, and the dashboard must not embed them in iframes or
request ports `5001`/`6006` during normal use.

## Services and URLs

Start the observability path from the repository root:

```powershell
docker compose -f infra/compose/docker-compose.yml build api-service training-worker
docker compose -f infra/compose/docker-compose.yml up -d --force-recreate api-service training-worker
docker compose -f infra/compose/docker-compose.yml up -d mlflow tensorboard
npm --prefix apps/frontend run dev -- --host 127.0.0.1 --port 5174
```

After a worker code fix, do not submit a validation job until the worker has
been rebuilt and recreated. Verify the expected source is in the running image
and inspect startup logs:

```powershell
docker compose -f infra/compose/docker-compose.yml exec training-worker python -c `
  "import inspect; from visiox_training_worker.train_entrypoint import capture_gradient_sample; print(inspect.getsource(capture_gradient_sample))"
docker compose -f infra/compose/docker-compose.yml logs --no-color --tail 100 training-worker api-service
```

| Service | Compose service | Observed container name | URL or role |
| --- | --- | --- | --- |
| VisioX UI | local Vite process | n/a | `http://127.0.0.1:5174/training-visualization` |
| VisioX API | `api-service` | `compose-api-service-1` | `http://127.0.0.1:8000` |
| Training worker | `training-worker` | `compose-training-worker-1` | Redis-backed worker, no host HTTP port |
| MLflow | `mlflow` | `compose-mlflow-1` | backend diagnostics at `http://127.0.0.1:5001` |
| TensorBoard | `tensorboard` | `compose-tensorboard-1` | backend diagnostics at `http://127.0.0.1:6006` |

The `compose-` container prefix comes from the current Compose project name;
use service names in commands so the runbook also works under another prefix.

Verify health and process state:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5001/
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:6006/
docker compose -f infra/compose/docker-compose.yml ps api-service training-worker mlflow tensorboard
```

## Submit a CPU Smoke Run

This creates a distinct two-Epoch detection pipeline and job from a ready
`yolo26n.pt` model and a validated dataset:

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
$pipeline = Invoke-RestMethod -Method Post -ContentType "application/json" -Body $pipelineBody `
  http://127.0.0.1:8000/pipelines

$jobBody = @{ params = @{}; environment = @{ device = "cpu" } } | ConvertTo-Json -Depth 4
$job = Invoke-RestMethod -Method Post -ContentType "application/json" -Body $jobBody `
  "http://127.0.0.1:8000/pipelines/$($pipeline.id)/jobs"
$jobId = $job.id
$pipeline.id
$jobId
```

Poll the actual job state instead of relying on a fixed sleep:

```powershell
do {
  $job = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId"
  $job | Select-Object id, status, started_at, finished_at
  if ($job.status -notin @("success", "succeeded", "failed", "canceled")) {
    Start-Sleep -Seconds 15
  }
} while ($job.status -notin @("success", "succeeded", "failed", "canceled"))
```

## Native API Checks

The browser should consume these VisioX endpoints only:

```powershell
$summary = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/summary"
$scalars = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/scalars?keys=train.box_loss,metrics.map50&max_points=2000"
$resources = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/resources"
$graph = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/graph"
$artifacts = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/artifacts"

$weightTag = $summary.available_histograms.weight | Select-Object -First 1
if ($weightTag) {
  $encodedTag = [uri]::EscapeDataString($weightTag)
  $histogram = Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/histograms?kind=weight&tag=$encodedTag&step=1"
}
```

Expected for a successful fresh two-Epoch run:

- `summary.status` is `success` or `succeeded`, progress is 100%, and Epoch is
  `2 / 2`.
- Scalar series contain real points for both Epochs.
- Resource series contain CPU and memory samples.
- Result artifacts and at least one weight histogram are present.
- Graph availability reflects the TensorBoard event file. Do not synthesize a
  graph when none was emitted.
- Gradient histograms are optional. Show them only when the installed
  Ultralytics lifecycle exposes gradients at the capture callback.
- A failed source is represented in `availability`; it must not turn the
  entire response into HTTP 500.

## Event Directory and Cache

The shared Docker volume is mounted read/write in the worker and read-only in
the API and TensorBoard containers. A job's files are expected at:

```text
/workspace/training-runs/runs/job-<training-job-id>/
```

Expected files can include `events.out.tfevents.*`, `visiox-progress.json`,
`results.csv`, result images, and weight files. TensorBoard watches
`/workspace/training-runs/runs` with a two-second reload interval.

The API caches TensorBoard `EventAccumulator` objects by `(run directory,
newest event-file mtime_ns)`. A changed newest mtime creates a fresh cache
entry. Entries are kept in LRU order and the application-scoped cache size is
controlled by `VISIOX_OBSERVABILITY_EVENT_CACHE_SIZE` (32 by default). Scalar
responses are independently capped at 2,000 points per series by default.

## Old and New Jobs

Jobs created before native collection callbacks may have database metrics and
MinIO artifacts but no TensorBoard event file, progress snapshot, resource
series, graph, or histograms. Keep those jobs selectable and display the real
`availability.reason` and empty states. Do not backfill fabricated telemetry.

Fresh jobs should emit the new event and progress files. Failed or canceled
jobs may retain partial data; for example, a graph can exist even when no
Epoch scalar or histogram was recorded. Treat each source independently.

## Missing-Source Diagnosis

Start with the native summary:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/training-jobs/$jobId/observability/summary" |
  ConvertTo-Json -Depth 10
```

Inspect worker failures and the shared directory:

```powershell
docker compose -f infra/compose/docker-compose.yml logs --no-color --tail 250 training-worker
docker compose -f infra/compose/docker-compose.yml exec training-worker sh -lc `
  "find /workspace/training-runs/runs/job-$jobId -maxdepth 1 -type f -printf '%f %s bytes\n' | sort"
```

Inspect TensorBoard tags using the API image's installed parser:

```powershell
docker compose -f infra/compose/docker-compose.yml exec api-service python -c `
  "from tensorboard.backend.event_processing.event_accumulator import EventAccumulator; p='/workspace/training-runs/runs/job-$jobId'; e=EventAccumulator(p, size_guidance={'histograms': 0}); e.Reload(); print(e.Tags())"
```

Inspect MLflow through its service URI, not a bare local CLI store:

```powershell
docker compose -f infra/compose/docker-compose.yml exec api-service python -c `
  "from mlflow import MlflowClient; c=MlflowClient('http://mlflow:5000'); exps=c.search_experiments(); print([(e.experiment_id,e.name) for e in exps]); print([(r.info.run_id,r.info.status,r.data.tags.get('mlflow.runName'),r.data.metrics) for r in c.search_runs([e.experiment_id for e in exps]) if r.data.tags.get('mlflow.runName')=='job-$jobId'])"
```

Also check service logs when a source is unavailable:

```powershell
docker compose -f infra/compose/docker-compose.yml logs --no-color --tail 200 api-service mlflow tensorboard
```

## Verification Commands

The production API image intentionally omits `pytest` and `tests/`. For a
Python 3.12 test run against the current workspace, use a disposable container,
mount the workspace, install only the test runner, and clear the Compose DSN so
test-managed SQLite migrations are not redirected to Postgres:

```powershell
docker compose -f infra/compose/docker-compose.yml run --rm `
  -e VISIOX_POSTGRES_DSN= `
  -v "${PWD}:/workspace" `
  -w /workspace `
  api-service sh -lc "pip install --no-cache-dir 'pytest>=8.3,<9.0' >/tmp/pytest-install.log && python -m pytest tests/unit/test_training_observability.py tests/unit/test_training_observability_service.py tests/integration/test_training_observability_api.py tests/integration/test_training_pipeline.py -q"

npm --prefix apps/frontend test -- --run tests/training-visualization-view.spec.ts tests/training-chart-components.spec.ts tests/model-space-view.spec.ts
npm --prefix apps/frontend run build
py -3.12 -m ruff check apps/api-service/src/visiox_api workers/training-worker/src/visiox_training_worker tests/unit tests/integration
npm --prefix apps/frontend run typecheck
docker compose -f infra/compose/docker-compose.yml config --quiet
git diff --check
docker compose -f infra/compose/docker-compose.yml ps
```

## Browser QA Notes

- Histogram empty states follow the active segment: `权重` displays
  `该训练未记录权重分布`, while `梯度` displays `该训练未记录梯度分布`.
- Graphs with more than 150 nodes hide node labels in the initial canvas view
  to keep dense desktop and mobile layouts readable. Zoom, pan, the node list,
  and the node detail inspector remain available.
- At the `390x844` viewport, record the node count and chart dimensions during
  QA and verify that graph navigation remains usable without horizontal page
  overflow.

## Latest Validation Status

The rebuilt stack passed an end-to-end CPU smoke run on 2026-07-14:

- Pipeline: `49fdc595-4937-47ba-a537-c6bd622d6229`
- Training job: `50c4cf1e-386d-41f8-9e06-affdd410a794`
- Configuration: YOLO26 detect nano, 2 Epochs, batch 1, image size 320, CPU
- Result: `success`, 2/2 Epochs, 100% progress
- Scalars: two training-loss points and validation metric points were returned
- Resources: CPU, memory percentage, memory usage, and image throughput each
  returned two samples
- Graph: 1,152 nodes and 2,953 edges
- Histograms: 366 weight tags and 366 gradient tags; Epoch 2 payloads were
  returned successfully
- Artifacts: `best.pt`, `last.pt`, four result images, and two batch previews

Desktop `1440x900` and mobile `390x844` browser checks passed for overview,
metrics, resources, graph, and histogram views. No iframe, horizontal page
overflow, console warning, or console error was observed. The focused frontend
Vitest run passed 34 tests across three files after the rebuild.
