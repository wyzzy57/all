# Multi-Framework Training Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scattered `engine` branches with a versioned framework adapter layer and deliver a production object-detection loop for PaddleX `PP-YOLOE-S` and `RT-DETR-L` without regressing Ultralytics or LLaMA-Factory.

**Architecture:** The control plane stores explicit task, framework, model-family, adapter, runtime-image, dataset, and artifact identities. Registered adapters resolve stable capability, dataset, launch, telemetry, evaluation, inference, and deployment contracts. Edge nodes continue to use SSH and Docker, but receive a framework-neutral signed `LaunchSpec`; each immutable runtime image interprets its own framework configuration. VisiOX remains the primary visualization UI while MLflow, TensorBoard, VisualDL, durable JSONL, and object storage act as telemetry and artifact sources.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, Redis Streams, MinIO, SSH, Docker, Vue 3, TypeScript, Element Plus, ECharts, Vitest, Ultralytics, LLaMA-Factory, PaddleX 3.0.3, PaddlePaddle 3.0.0 GPU CUDA 11.8, VisualDL, MLflow, TensorBoard.

---

## Delivery Rules

- Preserve `TrainingPipeline.engine` and existing API response fields during the migration window. New code reads explicit `framework` and falls back to the legacy engine mapping only for old rows.
- Map the product label `PP-YOLOE-S` to PaddleX runtime model id `PP-YOLOE_plus-S`; persist both the product label and resolved runtime id in the job snapshot.
- Pin every training and inference image by digest before a real job can be submitted. Tags are permitted only in local image-build commands, never in persisted jobs.
- Do not install PaddlePaddle or PaddleX into the API image or root Python environment. Their dependencies live only in PaddleX training/inference images.
- A pipeline may change framework only while it has no submitted job. The first successful job creation transaction locks `task_kind`, `framework`, and `adapter_key`.
- Retry and resume create a new immutable attempt. They never overwrite prior logs, metrics, snapshots, or artifacts.
- The native VisiOX view does not reproduce computation graphs or histograms. TensorBoard or VisualDL remains the advanced-debugging path.
- Use the official PaddleX object-detection contract: COCO detection data, `Global.mode=train|evaluate|predict`, `Global.dataset_dir`, `Global.output`, and `Train.epochs_iters`.

## Phase 1: Stable Domain Contracts

### Task 1: Add Canonical Framework Contracts and Registry

**Files:**
- Create: `packages/visiox-training/src/visiox_training/__init__.py`
- Create: `packages/visiox-training/src/visiox_training/contracts.py`
- Create: `packages/visiox-training/src/visiox_training/capabilities.py`
- Create: `packages/visiox-training/src/visiox_training/registry.py`
- Create: `packages/visiox-training/src/visiox_training/errors.py`
- Create: `packages/visiox-training/src/visiox_training/adapters/__init__.py`
- Create: `packages/visiox-training/src/visiox_training/adapters/base.py`
- Modify: `pyproject.toml`
- Create: `tests/unit/test_framework_contracts.py`
- Create: `tests/unit/test_framework_registry.py`

- [ ] Write failing serialization tests for `DatasetManifest`, `LaunchSpec`, `TelemetryEnvelope`, `ArtifactEntry`, and `ArtifactManifest`. Assert strict unknown-field rejection and stable SHA-256 canonical serialization.
- [ ] Write failing registry tests for duplicate adapter keys, unsupported task/framework combinations, unknown adapter versions, and legacy engine lookup.
- [ ] Add Pydantic contracts with explicit `schema_version`, `adapter_key`, and `adapter_version` fields. Keep commands as structured arguments and reject shell strings.

```python
class LaunchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    job_id: str
    attempt: int = Field(ge=1)
    task_kind: str
    framework: str
    adapter_key: str
    adapter_version: str
    runtime_image_digest: str
    model: ModelInput
    dataset: DatasetInput
    parameters: dict[str, JsonValue]
    allocation: ResourceAllocation
    output: OutputContract
```

- [ ] Define focused adapter protocols for capability, dataset, training, observability, evaluation/inference, and deployment operations. Do not create one class with optional methods.
- [ ] Implement `FrameworkAdapterRegistry` keyed by immutable `adapter_key`; register aliases `yolo26 -> ultralytics.object_detection.v1` and `llamafactory -> llamafactory.llm_sft.v1` only in a legacy lookup table.
- [ ] Add `packages/visiox-training/src` to Hatch wheel packages and pytest `pythonpath`.
- [ ] Run `pytest tests/unit/test_framework_contracts.py tests/unit/test_framework_registry.py -q`.

Expected: all contract and registry tests pass; invalid shell-like launch fields fail validation.

- [ ] Commit with `feat: add framework adapter contracts`.

### Task 2: Migrate Pipelines, Jobs, Attempts, and Model Artifacts

**Files:**
- Modify: `packages/visiox-db/src/visiox_db/models/model_space.py`
- Modify: `packages/visiox-db/src/visiox_db/models/__init__.py`
- Create: `infra/migrations/versions/20260731_0001_multi_framework_training.py`
- Modify: `tests/integration/test_migrations.py`
- Create: `tests/unit/test_framework_model_mapping.py`

- [ ] Write a migration test starting from revision `20260727_0006` with one `yolo26` pipeline/job/model and one `llamafactory` pipeline/job/model.
- [ ] Assert upgrade backfills the following mappings without fabricating artifacts or metrics:

```python
LEGACY_ENGINE_MAPPING = {
    "yolo26": ("object_detection", "ultralytics", "ultralytics.object_detection.v1"),
    "llamafactory": ("llm_sft", "llamafactory", "llamafactory.llm_sft.v1"),
}
```

- [ ] Extend `BaseModel` with `framework`, `model_family`, `variant`, `artifact_format`, and `artifact_metadata`.
- [ ] Extend `TrainingPipeline` with `task_kind`, `framework`, `adapter_key`, `adapter_version`, `model_family`, `recipe`, `framework_locked_at`, `first_submitted_job_id`, and `cloned_from_pipeline_id`.
- [ ] Extend `TrainingJob` with immutable `resolved_snapshot` and `launch_spec_checksum`.
- [ ] Add `TrainingJobAttempt` with `(training_job_id, attempt_number)` uniqueness, status, immutable launch spec, log URI, metrics, artifact manifest, container ids, and timestamps.
- [ ] Extend `TrainedModel` with `framework`, `adapter_key`, `model_family`, `model_format`, `artifact_role`, `checksum`, `size_bytes`, `evaluation_report_uri`, `deployment_compatibility`, `artifact_manifest`, and `display_name`.
- [ ] Replace the `BaseModel` uniqueness rule with `(framework, family, task, scale)` while retaining filename uniqueness.
- [ ] Keep new columns nullable during backfill, populate all legacy rows, then make required identity columns non-null.
- [ ] Run `pytest tests/integration/test_migrations.py tests/unit/test_framework_model_mapping.py -q`.

Expected: upgrade and downgrade complete, old rows are readable through explicit framework fields, and migration preserves row counts and ids.

- [ ] Commit with `feat: persist framework training identities`.

### Task 3: Publish a Capability-Driven Framework Catalog

**Files:**
- Create: `packages/visiox-training/src/visiox_training/adapters/ultralytics.py`
- Create: `packages/visiox-training/src/visiox_training/adapters/llamafactory.py`
- Create: `packages/visiox-training/src/visiox_training/adapters/paddlex.py`
- Create: `apps/api-service/src/visiox_api/services/framework_adapters.py`
- Create: `apps/api-service/src/visiox_api/routes/framework_capabilities.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Modify: `packages/visiox-common/src/visiox_common/settings.py`
- Modify: `.env.example`
- Create: `tests/unit/test_framework_capabilities.py`
- Create: `tests/integration/test_framework_capabilities_api.py`

- [ ] Write failing tests that request capabilities for `object_detection` and receive Ultralytics plus PaddleX, while `llm_sft` receives only LLaMA-Factory.
- [ ] Assert PaddleX exposes exactly two initial model options:

```python
PADDLEX_DETECTION_MODELS = (
    ModelCapability(label="PP-YOLOE-S", runtime_id="PP-YOLOE_plus-S", family="PP-YOLOE", variant="S"),
    ModelCapability(label="RT-DETR-L", runtime_id="RT-DETR-L", family="RT-DETR", variant="L"),
)
```

- [ ] Return parameter schemas, dataset formats, supported resource kinds, evaluation/inference/deployment capabilities, runtime version, and adapter version from `GET /frameworks/capabilities`.
- [ ] Add settings `ultralytics_training_image_digest`, `paddlex_training_image_digest`, `llm_training_image_digest`, and `paddlex_inference_image_digest`.
- [ ] Mark an adapter unavailable when its required digest is empty; still return it with `available=false` and an actionable reason so the UI can explain why submission is disabled.
- [ ] Run `pytest tests/unit/test_framework_capabilities.py tests/integration/test_framework_capabilities_api.py -q`.

Expected: the catalog is deterministic, authorized, and contains no framework choices for unsupported task combinations.

- [ ] Commit with `feat: expose framework capability catalog`.

### Task 4: Make Pipeline Creation Adapter-Driven and Enforce the Framework Lock

**Files:**
- Create: `apps/api-service/src/visiox_api/services/pipeline_configuration.py`
- Modify: `apps/api-service/src/visiox_api/routes/pipelines.py`
- Modify: `tests/integration/test_training_pipeline.py`
- Modify: `tests/integration/test_model_space_permissions.py`

- [ ] Add failing tests for creating Ultralytics, PaddleX, and LLaMA-Factory drafts using `task_kind`, `framework`, `adapter_key`, `model_family`, and `recipe`.
- [ ] Add a compatibility test proving legacy `{engine: "yolo26", task: "detect"}` requests still produce an Ultralytics pipeline.
- [ ] Add failing tests that allow framework changes before first job submission and return HTTP 409 after `framework_locked_at` is set.
- [ ] Add `POST /pipelines/{id}/clone` tests that preserve dataset and user-editable parameters, clear lock/job fields, and record `cloned_from_pipeline_id`.
- [ ] Move framework-specific validation out of the route into registered configuration adapters. Keep authorization and transaction handling in the route/service boundary.
- [ ] Return both legacy `engine` and explicit framework identity until all clients are migrated.
- [ ] Run `pytest tests/integration/test_training_pipeline.py tests/integration/test_model_space_permissions.py -q`.

Expected: all three frameworks create valid drafts, lock atomically on first submission, and old API clients remain functional.

- [ ] Commit with `refactor: route pipeline configuration through adapters`.

## Phase 2: Reproducible Training Execution

### Task 5: Resolve Immutable Job Snapshots and Attempt Records

**Files:**
- Create: `apps/api-service/src/visiox_api/services/training_submission.py`
- Modify: `apps/api-service/src/visiox_api/routes/training_jobs.py`
- Modify: `apps/api-service/src/visiox_api/services/resource_scheduler.py`
- Modify: `tests/integration/test_training_pipeline.py`
- Modify: `tests/unit/test_distributed_training_plan.py`

- [ ] Write failing tests that submit one job for each framework and compare the persisted `resolved_snapshot` to the selected model checksum/revision, dataset manifest checksum, normalized parameters, allocation, adapter version, and image digest.
- [ ] Assert request-time parameter dictionaries are copied and normalized; later pipeline edits cannot mutate an existing job snapshot.
- [ ] Assert job creation, attempt `1`, and pipeline framework lock happen in one transaction.
- [ ] Add retry/resume tests that create attempt `n+1`, retain earlier attempt rows, and reject a checkpoint from a different framework/model family.
- [ ] Implement `TrainingSubmissionService.resolve()` and compute `launch_spec_checksum` from canonical JSON.

```python
snapshot = ResolvedTrainingSnapshot(
    task_kind=pipeline.task_kind,
    framework=pipeline.framework,
    adapter_key=pipeline.adapter_key,
    adapter_version=adapter.version,
    runtime_image_digest=adapter.training_image_digest(settings),
    model=adapter.resolve_model(session, pipeline),
    dataset=adapter.resolve_dataset(session, storage, pipeline.dataset_id, dataset_version_id),
    parameters=adapter.normalize_parameters(request.params, pipeline.recipe),
    resource_request=adapter.normalize_resource_request(request.distributed),
    allocation=allocation,
)
```

- [ ] Store only ids in Redis `EDGE_TRAIN` commands; the executor reloads the immutable attempt and snapshot from PostgreSQL.
- [ ] Run `pytest tests/integration/test_training_pipeline.py tests/unit/test_distributed_training_plan.py -q`.

Expected: snapshots are stable across process restarts and retries create new attempts without changing attempt 1.

- [ ] Commit with `feat: freeze training job snapshots`.

### Task 6: Replace Engine-Specific Remote Scripts with a Framework-Neutral LaunchSpec

**Files:**
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/distributed_execution.py`
- Modify: `workers/edge-executor-worker/remote/stage_training.sh`
- Modify: `workers/edge-executor-worker/remote/launch_rank.sh`
- Modify: `workers/edge-executor-worker/remote/stop_training.sh`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/scripts.py`
- Modify: `tests/unit/test_distributed_training_plan.py`
- Modify: `tests/unit/test_edge_acceptance_scripts.py`

- [ ] Freeze current Ultralytics and LLaMA behavior with characterization tests before changing request shapes.
- [ ] Write failing tests for a generic staging request containing `launch_spec`, dataset/model/checkpoint artifacts, and a runtime image digest without an `ENGINES` allowlist.
- [ ] Write failing tests that reject unknown schema versions, digest-less images, unsafe artifact paths, checksum mismatches, host paths, arbitrary entrypoints, and newline-bearing arguments.
- [ ] Make every training image expose `/usr/local/bin/visiox-train`; the remote launcher runs only that fixed entrypoint and mounts a read-only `/workspace/input/launch-spec.json`.
- [ ] Keep rank, node, GPU UUID, rendezvous, and output paths in the signed launch spec; framework images interpret their own parameter block.
- [ ] Generalize collectible artifacts from a hard-coded filename set to an `ArtifactManifest` supplied by the trusted adapter and bounded by count, size, safe relative paths, and SHA-256.
- [ ] Preserve container labels `training-job-id`, `attempt`, `node-rank`, `framework`, `adapter-key`, and `launch-spec-checksum` for reconciliation.
- [ ] Run `pytest tests/unit/test_distributed_training_plan.py tests/unit/test_edge_acceptance_scripts.py -q`.

Expected: existing Ultralytics/LLaMA launch tests pass through the new contract and a PaddleX launch reaches the fixed image entrypoint without framework branches in shell scripts.

- [ ] Commit with `refactor: generalize remote training launch`.

### Task 7: Build the PaddleX COCO Dataset Adapter

**Files:**
- Create: `packages/visiox-paddlex/src/visiox_paddlex/__init__.py`
- Create: `packages/visiox-paddlex/src/visiox_paddlex/datasets.py`
- Create: `packages/visiox-paddlex/src/visiox_paddlex/config.py`
- Modify: `pyproject.toml`
- Create: `tests/integration/test_paddlex_dataset_adapter.py`
- Create: `tests/fixtures/paddlex_detection/expected_manifest.json`

- [ ] Write failing fixture tests for a detection dataset with train/val/test splits, empty images, multiple boxes, Unicode labels, and stable category ids.
- [ ] Export a PaddleX-compatible COCO layout with `annotations/instance_train.json`, `annotations/instance_val.json`, `annotations/instance_test.json`, and copied or streamed image files.
- [ ] Preserve source dataset/version ids and sample checksums in `dataset-manifest.json`; compute a whole-manifest checksum after writing all files.
- [ ] Reject datasets without imported annotations, invalid boxes, missing images, zero train samples, or zero val samples before a remote job is queued.
- [ ] Generate PaddleX config overrides from typed parameters; never concatenate user-supplied shell fragments.
- [ ] Run `pytest tests/integration/test_paddlex_dataset_adapter.py -q`.

Expected: exported COCO JSON is deterministic and accepted by the adapter precheck for both PaddleX models.

- [ ] Commit with `feat: add PaddleX detection dataset adapter`.

### Task 8: Add the Isolated PaddleX Training Worker and Image

**Files:**
- Create: `workers/paddlex-training-worker/Dockerfile.edge`
- Create: `workers/paddlex-training-worker/requirements.lock`
- Create: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/__init__.py`
- Create: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/entrypoint.py`
- Create: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/config.py`
- Create: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/telemetry.py`
- Create: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/resources.py`
- Create: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/artifacts.py`
- Modify: `pyproject.toml`
- Create: `tests/unit/test_paddlex_training_config.py`
- Create: `tests/unit/test_paddlex_training_worker.py`
- Create: `tests/unit/test_paddlex_training_telemetry.py`

- [ ] Write config tests mapping `PP-YOLOE-S -> paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml` and `RT-DETR-L -> paddlex/configs/modules/object_detection/RT-DETR-L.yaml`.
- [ ] Write tests for epochs, batch size, learning rate, image size, workers, AMP, device list, resume checkpoint, output path, and rejection of managed-field overrides.
- [ ] Build commands as argument arrays equivalent to:

```text
python main.py -c paddlex/configs/modules/object_detection/RT-DETR-L.yaml
-o Global.mode=train
-o Global.dataset_dir=/workspace/dataset
-o Global.output=/workspace/output
-o Global.device=gpu:0
-o Train.epochs_iters=2
```

- [ ] Base the image on the official CUDA 11.8 PaddlePaddle 3.0.0 image and pin `paddlex==3.0.3`; install worker telemetry dependencies from `requirements.lock`.
- [ ] Enable evaluation during training and VisualDL output. Tail PaddleX/PaddleDetection logs into canonical `visiox-metrics.jsonl`, mirror normalized metrics to MLflow and TensorBoard, and preserve raw VisualDL logs as artifacts.
- [ ] Emit atomic `visiox-progress.json`, per-GPU `resource_metrics.jsonl`, durable stdout/stderr logs, and `artifact-manifest.json` on success, failure, and SIGTERM.
- [ ] Parse and package `train_result.json`, `train.log`, `config.yaml`, best dynamic weights, best static inference bundle, last/checkpoint weights, evaluation report, and visualization images.
- [ ] Run `pytest tests/unit/test_paddlex_training_config.py tests/unit/test_paddlex_training_worker.py tests/unit/test_paddlex_training_telemetry.py -q`.

Expected: command/config generation and telemetry work without importing PaddleX in the test process; subprocess calls are mocked at the worker boundary.

- [ ] Build `docker build -f workers/paddlex-training-worker/Dockerfile.edge -t visiox/paddlex-training:test .`.

Expected: image build completes and `docker run --rm visiox/paddlex-training:test python -c "import paddle,paddlex; print(paddle.__version__)"` prints PaddlePaddle `3.0.0`.

- [ ] Commit with `feat: add PaddleX training runtime`.

### Task 9: Collect Framework-Neutral Artifacts and Complete Jobs Reliably

**Files:**
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/distributed_execution.py`
- Modify: `apps/api-service/src/visiox_api/routes/training_jobs.py`
- Create: `apps/api-service/src/visiox_api/services/training_artifacts.py`
- Modify: `tests/unit/test_distributed_training_plan.py`
- Modify: `tests/integration/test_training_pipeline.py`
- Modify: `tests/integration/test_trained_models_api.py`

- [ ] Write failing tests that ingest Ultralytics, PaddleX, and LLaMA artifact manifests into typed `TrainedModel` rows without relying on `best.pt` and `last.pt` filenames.
- [ ] Require one deployable `best` artifact for a successful detection job; mark artifact collection failures as `failed` with a durable code instead of reporting training success.
- [ ] Preserve framework-native artifacts and add display aliases without renaming physical files or changing checksums.
- [ ] Make reconciliation idempotent: collecting the same manifest twice must not duplicate models or overwrite a different attempt.
- [ ] Derive pipeline status from the active job/attempt state and transition through `evaluating` and `artifact_collecting` before `succeeded`.
- [ ] Run `pytest tests/unit/test_distributed_training_plan.py tests/integration/test_training_pipeline.py tests/integration/test_trained_models_api.py -q`.

Expected: all framework artifacts are addressable by role, checksum, and attempt after API and worker restarts.

- [ ] Commit with `feat: ingest canonical training artifacts`.

## Phase 3: Evaluation, Inference, Deployment, and Observability

### Task 10: Normalize Observability Across Ultralytics, PaddleX, and LLaMA-Factory

**Files:**
- Modify: `apps/api-service/src/visiox_api/services/observability/base.py`
- Modify: `apps/api-service/src/visiox_api/services/observability/ultralytics.py`
- Modify: `apps/api-service/src/visiox_api/services/observability/llamafactory.py`
- Create: `apps/api-service/src/visiox_api/services/observability/paddlex.py`
- Modify: `apps/api-service/src/visiox_api/services/training_observability.py`
- Modify: `apps/api-service/src/visiox_api/routes/training_observability.py`
- Modify: `tests/unit/test_training_observability_service.py`
- Create: `tests/unit/test_paddlex_observability.py`
- Modify: `tests/integration/test_training_observability_api.py`

- [ ] Characterize existing Ultralytics and LLaMA responses before refactoring `for_engine()` into registry lookup.
- [ ] Extend scalar points with `canonical_name`, `raw_name`, `unit`, `split`, `step`, `epoch`, `timestamp`, and `source`; preserve the current response shape through compatibility fields.
- [ ] Add PaddleX parsing for total/model losses, learning rate, AP, AP50, AP75, APS, APM, APL, and AR from MLflow, TensorBoard mirrors, VisualDL/raw logs, and JSONL fallback.
- [ ] Keep loss families in separate charts and combine train/val only for the same canonical metric and unit.
- [ ] Expose source availability for MLflow, TensorBoard, VisualDL, progress, resources, logs, and artifacts without failing the whole page when one source is unavailable.
- [ ] Remove graph/histogram data from the native page contract; retain external TensorBoard/VisualDL links as secondary actions.
- [ ] Run `pytest tests/unit/test_training_observability_service.py tests/unit/test_paddlex_observability.py tests/integration/test_training_observability_api.py -q`.

Expected: all three adapters return the same summary/scalars/resources/analysis/artifacts envelope and preserve framework-specific raw metric names.

- [ ] Commit with `feat: normalize multi-framework observability`.

### Task 11: Route Evaluation and Image Inference Through Adapters

**Files:**
- Create: `apps/api-service/src/visiox_api/services/pipeline_evaluation.py`
- Create: `apps/api-service/src/visiox_api/services/pipeline_inference.py`
- Modify: `apps/api-service/src/visiox_api/routes/pipeline_evaluation.py`
- Modify: `apps/api-service/src/visiox_api/routes/pipeline_inference.py`
- Create: `packages/visiox-paddlex/src/visiox_paddlex/results.py`
- Modify: `tests/integration/test_pipeline_evaluation.py`
- Modify: `tests/integration/test_pipeline_inference.py`

- [ ] Freeze current Ultralytics API behavior with tests, then add PaddleX cases using fake adapter subprocesses/results.
- [ ] Resolve weights by artifact role and manifest format rather than `.pt` name.
- [ ] For PaddleX evaluation, invoke the pinned runtime with `Global.mode=evaluate`, the matching model config, dataset path, and `Evaluate.weight_path`; parse `evaluate_result.json` into canonical COCO metrics.
- [ ] For PaddleX image inference, load the exported static inference bundle with `paddlex.create_model(model_dir=...)`, return normalized boxes, and return the framework-rendered annotated image.
- [ ] Reject model/dataset/framework mismatches before downloading large artifacts.
- [ ] Run `pytest tests/integration/test_pipeline_evaluation.py tests/integration/test_pipeline_inference.py -q`.

Expected: the same API endpoints evaluate and predict with Ultralytics or PaddleX according to the persisted adapter identity.

- [ ] Commit with `feat: add PaddleX evaluation and inference adapters`.

### Task 12: Add PaddleX High-Performance Deployment Compatibility

**Files:**
- Create: `apps/paddlex-inference/Dockerfile`
- Create: `apps/paddlex-inference/src/visiox_paddlex_inference/__init__.py`
- Create: `apps/paddlex-inference/src/visiox_paddlex_inference/config.py`
- Create: `apps/paddlex-inference/src/visiox_paddlex_inference/main.py`
- Create: `apps/paddlex-inference/src/visiox_paddlex_inference/predict.py`
- Create: `apps/api-service/src/visiox_api/services/deployment_adapters.py`
- Modify: `apps/api-service/src/visiox_api/routes/services.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/deployment.py`
- Modify: `workers/edge-executor-worker/remote/deploy_inference.sh`
- Modify: `workers/edge-executor-worker/remote/inspect_deployment.sh`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/reconciliation.py`
- Modify: `pyproject.toml`
- Create: `tests/integration/test_paddlex_inference_api.py`
- Modify: `tests/integration/test_services_api.py`
- Modify: `tests/unit/test_edge_deployment.py`
- Modify: `tests/unit/test_edge_reconciliation.py`

- [ ] Write an HTTP compatibility test requiring `/health`, `/metadata`, and `/predict/image` to match the existing service contract.
- [ ] Package the PaddleX static inference directory as one checksummed bundle and identify it as `paddle_inference_bundle` in `ArtifactManifest`.
- [ ] Add deployment adapters that resolve runtime image, artifact mount/extraction, device, precision, input size, and optimization mode from the trained model compatibility matrix.
- [ ] Map automatic PaddleX GPU optimization to PaddleX high-performance inference with TensorRT when node inventory is compatible; fall back to Paddle Inference and record the resolved backend.
- [ ] Extend edge deployment state and labels with framework, adapter key, model format, resolved backend, runtime digest, and model checksum.
- [ ] Preserve stop/start/rollback/reconcile semantics for both inference images.
- [ ] Run `pytest tests/integration/test_paddlex_inference_api.py tests/integration/test_services_api.py tests/unit/test_edge_deployment.py tests/unit/test_edge_reconciliation.py -q`.

Expected: a PaddleX service survives platform restart, reports its resolved backend, and serves a real annotated prediction through the existing service experience page.

- [ ] Commit with `feat: deploy PaddleX detection services`.

## Phase 4: Capability-Driven Product UI

### Task 13: Make the Pipeline Wizard Select Task, Framework, and Model Explicitly

**Files:**
- Modify: `apps/frontend/src/api/client.ts`
- Create: `apps/frontend/src/features/pipeline-wizard/frameworkCatalog.ts`
- Create: `apps/frontend/src/features/pipeline-wizard/FrameworkModelSelector.vue`
- Modify: `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- Modify: `apps/frontend/src/features/pipeline-wizard/PipelineWizardShell.vue`
- Modify: `apps/frontend/src/features/pipeline-wizard/usePipelineWizardDraft.ts`
- Create: `apps/frontend/tests/framework-model-selector.spec.ts`
- Modify: `apps/frontend/tests/model-space-view.spec.ts`
- Modify: `apps/frontend/tests/pipeline-wizard-draft.spec.ts`

- [ ] Write tests proving the wizard loads capabilities, shows Ultralytics and PaddleX for object detection, shows only LLaMA-Factory for LLM SFT, and renders only compatible models.
- [ ] Add typed frontend records for framework capabilities, adapter identity, lock fields, resolved snapshots, attempts, and artifact manifests.
- [ ] Replace `task === "llm" ? "llamafactory" : "yolo26"` with user selection backed by the capability API.
- [ ] Show `PP-YOLOE-S` and `RT-DETR-L` with framework badges, runtime availability, supported dataset format, and resource requirements.
- [ ] Generate common parameter controls from capability metadata and load a framework-specific advanced YAML editor that validates through the API before saving.
- [ ] Disable framework/model changes after lock, show the reason, and expose a `克隆并更换框架` action.
- [ ] Keep stable dimensions and responsive grid constraints so browser zoom changes density but not control order or overlap.
- [ ] Run `npm --prefix apps/frontend test -- framework-model-selector.spec.ts model-space-view.spec.ts pipeline-wizard-draft.spec.ts`.

Expected: selected framework and model survive draft autosave/reload, and a locked pipeline cannot be altered from the UI.

- [ ] Commit with `feat: add framework-aware pipeline wizard`.

### Task 14: Present One Native Visualization Shell for All Three Frameworks

**Files:**
- Modify: `apps/frontend/src/api/client.ts`
- Modify: `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`
- Modify: `apps/frontend/src/components/training/trainingMetricCatalog.ts`
- Create: `apps/frontend/src/features/training-observability/paddlex/paddlexMetricCatalog.ts`
- Create: `apps/frontend/src/features/training-observability/paddlex/PaddleXTrainingAnalysis.vue`
- Modify: `apps/frontend/src/features/training-observability/llm/llmMetricCatalog.ts`
- Modify: `apps/frontend/src/components/training/TrainingRunComparison.vue`
- Create: `apps/frontend/tests/paddlex-training-observability.spec.ts`
- Modify: `apps/frontend/tests/training-visualization-view.spec.ts`
- Modify: `apps/frontend/tests/training-run-comparison.spec.ts`
- Modify: `apps/frontend/tests/training-metric-catalog.spec.ts`

- [ ] Write tests for framework badge, model, dataset version, image digest, adapter version, source availability, attempt selection, and artifact download.
- [ ] Render common tabs `概览`, `指标`, `资源`, `分析`, `日志`, and `产物` for every framework.
- [ ] Use one chart per semantic metric by default. Put train/val of the same metric together; keep loss, ratio/AP, learning rate, throughput, utilization, memory, temperature, and power on separate unit-aware charts.
- [ ] Add PaddleX groups for optimization, COCO quality, model-specific losses, samples, evaluation report, and raw VisualDL link.
- [ ] Allow cross-framework comparison only for canonical metrics such as AP50, AP50-95, precision, recall, throughput, runtime, and resource peaks. Disable proprietary-loss comparison with an explanation.
- [ ] Keep MLflow, TensorBoard, and VisualDL as secondary external tools; no native graph or histogram panels.
- [ ] Run `npm --prefix apps/frontend test -- paddlex-training-observability.spec.ts training-visualization-view.spec.ts training-run-comparison.spec.ts training-metric-catalog.spec.ts`.
- [ ] Run `npm --prefix apps/frontend run typecheck` and `npm --prefix apps/frontend run build`.

Expected: all three frameworks render in the same stable shell, chart axes are semantically correct, and framework-specific artifacts remain accessible.

- [ ] Commit with `feat: visualize all training frameworks`.

## Phase 5: Packaging, Compatibility, and Production Acceptance

### Task 15: Wire Images, Compose Configuration, Seeds, and Documentation

**Files:**
- Modify: `infra/compose/docker-compose.yml`
- Modify: `infra/compose/docker-compose.dev.yml`
- Modify: `infra/compose/docker-compose.server.yml`
- Modify: `infra/compose/docker-compose.production-mtls.yml`
- Modify: `infra/seed/yolo26_base_models.json`
- Create: `infra/seed/paddlex_base_models.json`
- Modify: `apps/api-service/src/visiox_api/seed_base_models.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `docs/operations/multi-framework-runtime-images.md`
- Create: `docs/operations/paddlex-object-detection-runbook.md`
- Modify: `tests/integration/test_base_model_seed.py`
- Modify: `tests/integration/test_migrations.py`

- [ ] Add seed tests for two PaddleX models with official source ids, config paths, artifact formats, and checksums/revisions; do not download weights in API startup.
- [ ] Document image build, registry push, digest resolution, environment configuration, compatibility matrix, rollback, cache location, and edge-node prerequisites.
- [ ] Add compose environment variables for all training/inference digests and public VisualDL URL only if a VisualDL service is enabled.
- [ ] Add healthchecks for API, MLflow, TensorBoard, and optional VisualDL while keeping training framework images as on-demand edge images rather than always-running platform services.
- [ ] Run `pytest tests/integration/test_base_model_seed.py tests/integration/test_migrations.py -q`.
- [ ] Run `docker compose -f infra/compose/docker-compose.yml config`.

Expected: compose resolves without warnings, startup performs no large PaddleX download, and the operations guide contains exact digest-pinning commands.

- [ ] Commit with `chore: package multi-framework runtimes`.

### Task 16: Run Regression and Real GPU Acceptance

**Files:** Modify only files from prior tasks when a verified defect is found.

- [ ] Run the backend regression suite:

```powershell
pytest tests/unit tests/integration -q
```

Expected: zero failures; tests requiring an external GPU remain explicitly marked and are not silently skipped in the acceptance profile.

- [ ] Run frontend verification:

```powershell
npm --prefix apps/frontend test
npm --prefix apps/frontend run typecheck
npm --prefix apps/frontend run build
```

Expected: Vitest, Vue type checking, and production build all pass.

- [ ] Build and push the PaddleX training and inference images to the configured registry, inspect their immutable digests, and store those digests in platform configuration.
- [ ] On an authorized x86 NVIDIA GPU node, run a two-epoch `PP-YOLOE-S` job using a real validated COCO dataset. Verify live logs, loss, learning rate, GPU/CPU/RAM metrics, AP metrics, VisualDL/MLflow/TensorBoard sources, best/static artifacts, evaluation, image inference, deployment, stop/start, and restart reconciliation.
- [ ] Repeat the full two-epoch loop for `RT-DETR-L`.
- [ ] Restart API, edge executor, Redis, and frontend while one job is active. Verify job/attempt identity, progress, logs, metrics, and final artifacts remain correct.
- [ ] Clone a completed PaddleX pipeline to Ultralytics with the same dataset. Verify the original framework remains locked and compare only canonical AP/resource metrics.
- [ ] Temporarily make MLflow unavailable and verify training continues with TensorBoard/VisualDL/JSONL fallback; restore MLflow and verify source availability recovers.
- [ ] Capture the final image digests, job ids, model checksums, evaluation ids, deployment service ids, and HTTP prediction responses in `docs/operations/paddlex-object-detection-runbook.md`.
- [ ] Commit verified fixes and evidence with `test: verify PaddleX production loop`.

## Final Review Gate

- [ ] Confirm every framework-specific control-plane branch is behind the adapter registry; remaining `engine` reads exist only in the documented compatibility layer and migration tests.
- [ ] Confirm all persisted jobs include adapter version, framework version, image digest, model revision/checksum, dataset manifest checksum, allocation, and launch-spec checksum.
- [ ] Confirm no API container imports PaddlePaddle or PaddleX.
- [ ] Confirm no user input can set a shell command, container entrypoint, host path, image tag, or unchecked artifact path.
- [ ] Confirm pipeline framework lock and clone behavior are enforced in API and UI.
- [ ] Confirm native charts never mix incompatible units or framework-proprietary loss definitions.
- [ ] Confirm stop, resume, failure, reconciliation, evaluation, inference, deployment, rollback, logs, and artifact downloads work for both PaddleX models.
- [ ] Run `git status --short` and include only intended source, tests, migrations, docs, and lock files in the final commit.

## Official Implementation References

- PaddleX object detection module: `https://paddlepaddle.github.io/PaddleX/3.4/en/module_usage/tutorials/cv_modules/object_detection.html`
- PaddlePaddle Docker/runtime compatibility: `https://paddlepaddle.github.io/PaddleX/latest/en/installation/paddlepaddle_install.html`
- PaddleX VisualDL integration: `https://paddlepaddle.github.io/PaddleX/3.4/en/VisualDL.html`
- PaddleDetection VisualDL training flags: `https://github.com/PaddlePaddle/PaddleDetection/blob/master/docs/tutorials/QUICK_STARTED.md`

