# LLM Training Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a dedicated native VisiOX overview, metric, resource, and analysis experience for real LLaMA-Factory SFT jobs using MLflow, TensorBoard, progress snapshots, durable logs, checkpoints, and artifacts.

**Architecture:** Keep MLflow and TensorBoard as telemetry stores while the API normalizes engine-specific data into stable VisiOX responses. Have the remote LLM worker emit a shared job identity, high-frequency TensorBoard events, MLflow parameters/metrics, resource samples, progress snapshots, and artifact manifests.

**Tech Stack:** LLaMA-Factory, MLflow, TensorBoard EventAccumulator, psutil, nvidia-smi, MinIO, FastAPI, Vue 3, ECharts, Vitest.

---

### Task 1: Standardize LLM Run Identity and Telemetry Output

**Files:**
- Modify: `workers/llm-training-worker/src/visiox_llm_training_worker/entrypoint.py`
- Modify: `workers/llm-training-worker/src/visiox_llm_training_worker/config.py`
- Modify: `apps/api-service/src/visiox_api/services/llm_training.py`
- Modify: `tests/unit/test_llm_training_worker.py`
- Modify: `tests/unit/test_llm_training.py`

- [ ] Write failing tests for deterministic MLflow run name, required tags, TensorBoard log directory, job progress file, metric JSONL, and artifact manifest references.
- [ ] Generate run name `visiox-{training_job_id}` and tags for organization, user, pipeline, job, node, model revision, dataset version/checksum, and training image digest.
- [ ] Force `report_to` and output paths from the system launch spec; reject user overrides of managed paths and identities.
- [ ] Write atomic `visiox-progress.json` and append-only `trainer_log.jsonl` with step, epoch, total steps, latest metrics, checkpoint, and timestamps.
- [ ] Run tests and commit with `feat: standardize LLM telemetry identity`.

### Task 2: Emit MLflow and TensorBoard Metrics

**Files:**
- Create: `workers/llm-training-worker/src/visiox_llm_training_worker/telemetry.py`
- Modify: `workers/llm-training-worker/src/visiox_llm_training_worker/entrypoint.py`
- Create: `tests/unit/test_llm_training_telemetry.py`

- [ ] Write tests for train/eval loss, learning rate, grad norm, epoch, step, tokens/s, samples/s, duplicate step handling, and MLflow outage fallback.
- [ ] Implement one callback that writes normalized metrics to TensorBoard and MLflow while preserving LLaMA-Factory native events.
- [ ] Buffer temporary MLflow failures to metric JSONL and continue training; mark source availability without failing the job solely because MLflow is unavailable.
- [ ] Flush and close writers on success, failure, SIGTERM, and safe stop.
- [ ] Run tests and commit with `feat: emit LLM training metrics`.

### Task 3: Capture Real Node and GPU Resource Samples

**Files:**
- Create: `workers/llm-training-worker/src/visiox_llm_training_worker/resources.py`
- Modify: `workers/llm-training-worker/src/visiox_llm_training_worker/entrypoint.py`
- Create: `tests/unit/test_llm_resource_sampling.py`

- [ ] Write tests for CPU, RAM, disk, network, per-GPU utilization/VRAM/temperature/power, unavailable nvidia-smi, and sampler shutdown.
- [ ] Sample every five seconds into `resource_metrics.jsonl` using psutil and controlled `nvidia-smi --query-gpu` arguments.
- [ ] Identify GPUs by UUID/index and never merge devices into one series.
- [ ] Include the latest sample in the atomic progress snapshot and package the full JSONL as an artifact.
- [ ] Run tests and commit with `feat: capture LLM training resources`.

### Task 4: Add Engine-Aware Observability Adapters

**Files:**
- Refactor: `apps/api-service/src/visiox_api/services/training_observability.py`
- Create: `apps/api-service/src/visiox_api/services/observability/base.py`
- Create: `apps/api-service/src/visiox_api/services/observability/ultralytics.py`
- Create: `apps/api-service/src/visiox_api/services/observability/llamafactory.py`
- Modify: `tests/unit/test_training_observability_service.py`
- Create: `tests/unit/test_llm_observability_service.py`

- [ ] Freeze existing Ultralytics responses with characterization tests before refactoring.
- [ ] Define adapter methods `summary`, `scalars`, `resources`, `analysis`, and `artifacts`.
- [ ] Move current CV behavior into the Ultralytics adapter without changing API output.
- [ ] Implement the LLaMA-Factory adapter with precedence MLflow, TensorBoard, metric/resource JSONL, then progress snapshot.
- [ ] Normalize aliases while retaining source and availability metadata for each series.
- [ ] Run both adapter test files and commit with `refactor: add engine-aware training observability`.

### Task 5: Implement Deterministic LLM Training Analysis

**Files:**
- Create: `apps/api-service/src/visiox_api/services/llm_training_analysis.py`
- Create: `tests/unit/test_llm_training_analysis.py`

- [ ] Write tests for converging loss, widening train/eval gap, non-finite loss, exploding grad norm, inactive scheduler, low GPU utilization, data-loader bottleneck, OOM, failed checkpoint, and insufficient evidence.
- [ ] Return findings with `code`, `severity`, `title`, `message`, `metric_names`, `step_range`, and observed values.
- [ ] Use explicit thresholds from settings and emit no diagnosis when minimum sample counts are not met.
- [ ] Never generate free-form unsupported conclusions.
- [ ] Run tests and commit with `feat: analyze LLM training health`.

### Task 6: Extend Observability APIs

**Files:**
- Modify: `apps/api-service/src/visiox_api/routes/training_observability.py`
- Modify: `apps/api-service/src/visiox_api/routes/training_jobs.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Modify: `tests/integration/test_training_observability_api.py`

- [ ] Write failing tests for LLM summary, metrics, resources, analysis, artifacts, source degradation, authorization, completed run history, and invalid range requests.
- [ ] Add `analysis` and `artifacts` routes and engine information to summary.
- [ ] Require inherited `view` permission on the training pipeline/job for all observability data and durable logs.
- [ ] Ensure stop/resume/retry actions preserve run attempt history and create a new MLflow run per attempt linked by tags.
- [ ] Run tests and commit with `feat: expose LLM training observability`.

### Task 7: Add LLM Metric Catalog and Native Components

**Files:**
- Create: `apps/frontend/src/features/training-observability/llm/llmMetricCatalog.ts`
- Create: `apps/frontend/src/features/training-observability/llm/LlmTrainingOverview.vue`
- Create: `apps/frontend/src/features/training-observability/llm/LlmTrainingMetrics.vue`
- Create: `apps/frontend/src/features/training-observability/llm/LlmTrainingResources.vue`
- Create: `apps/frontend/src/features/training-observability/llm/LlmTrainingAnalysis.vue`
- Create: `apps/frontend/tests/llm-training-observability.spec.ts`

- [ ] Write tests for metric grouping, separate y-axes by unit, series toggles, tooltips, zoom, empty/degraded sources, per-GPU series, analysis evidence, checkpoints, and artifacts.
- [ ] Use four native tabs: 概览, 指标, 资源, 分析.
- [ ] Group loss, optimization, throughput, and progress into separate charts; do not put all metrics on one scale.
- [ ] Show source availability unobtrusively and provide MLflow/TensorBoard deep links only as secondary actions.
- [ ] Run focused tests and commit with `feat: add native LLM observability components`.

### Task 8: Integrate the Dedicated LLM View

**Files:**
- Modify: `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`
- Modify: `apps/frontend/src/api/client.ts`
- Modify: `apps/frontend/tests/training-visualization-view.spec.ts`

- [ ] Write tests that select components by `engine`, preserve existing CV metrics, poll active jobs, stop polling terminal jobs, switch runs, delete permitted records, and open logs/artifacts.
- [ ] Split the oversized view into shared run selector plus CV and LLM feature components.
- [ ] Add typed analysis/artifact API responses and retain cursor/range parameters.
- [ ] Keep chart dimensions stable during loading, empty, error, and refresh states.
- [ ] Run tests, type checking, build, and commit with `feat: integrate LLM training visualization`.

### Task 9: Verify a Real SFT Run

**Files:** Modify only files above when defects appear.

- [ ] Run all LLM worker, telemetry, resource, observability, analysis, API, and frontend tests.
- [ ] Run a two-epoch QLoRA or LoRA smoke job on an authorized NVIDIA node.
- [ ] Verify live train/eval loss, learning rate, grad norm, tokens/s, samples/s, each GPU, CPU/RAM, logs, checkpoints, and final artifacts.
- [ ] Disconnect MLflow temporarily and verify TensorBoard/progress fallback continues; restore MLflow and confirm availability returns.
- [ ] Complete the job and verify the run remains fully inspectable after worker and platform restarts.
- [ ] Commit verified fixes with `test: verify LLM training observability`.
