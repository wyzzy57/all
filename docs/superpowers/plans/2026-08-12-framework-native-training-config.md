# Framework-Native Training Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 PaddleX 与 Ultralytics 目标检测训练提供模型专属的表单/YAML 双模式配置，并保证最终原生配置经过服务端校验、冻结并被真实 Worker 使用。

**Architecture:** 在 `visiox-training` 中建立框架无关的 `ConfigProfile` 契约与模型专属 Profile 注册表；API 负责模板获取、草稿持久化、校验和最终配置解析；前端通过一个独立目标检测配置组件编辑同一份结构化草稿；训练提交把不可变原生配置写入任务快照，两个 Worker 仅消费后端解析后的配置文件。旧扁平参数在迁移窗口内双读，但不再作为最终能力边界。

**Tech Stack:** Python 3.12、Pydantic v2、FastAPI、SQLAlchemy/Alembic、PyYAML、Vue 3、TypeScript、Element Plus、`yaml` npm 包、Vitest、pytest、PaddleX、Ultralytics

---

## File Structure

### New files

- `packages/visiox-training/src/visiox_training/configuration.py`: immutable config profile, draft, validation, merge and hash contracts.
- `packages/visiox-training/src/visiox_training/config_profiles/__init__.py`: profile registry entrypoint.
- `packages/visiox-training/src/visiox_training/config_profiles/paddlex.py`: PaddleX model-native templates and field mappings.
- `packages/visiox-training/src/visiox_training/config_profiles/ultralytics.py`: Ultralytics native defaults and field mappings.
- `apps/api-service/src/visiox_api/routes/framework_configuration.py`: profile, validation, draft and resolved-preview endpoints.
- `apps/api-service/src/visiox_api/services/framework_configuration.py`: authorization-aware draft/resolve orchestration.
- `infra/migrations/versions/20260812_0001_framework_config_drafts.py`: pipeline draft storage.
- `apps/frontend/src/features/pipeline-wizard/ObjectDetectionTrainingConfig.vue`: form/YAML dual-mode UI.
- `apps/frontend/src/features/pipeline-wizard/frameworkConfigDraft.ts`: immutable draft transforms and YAML synchronization helpers.
- `apps/frontend/tests/object-detection-training-config.spec.ts`: focused component tests.
- `tests/unit/test_framework_configuration.py`: core config contract and profile tests.
- `tests/integration/test_framework_configuration_api.py`: API, authorization and persistence tests.
- `tests/integration/test_native_training_configuration.py`: submission snapshot and worker handoff tests.

### Modified files

- `packages/visiox-training/src/visiox_training/__init__.py`: export configuration contracts.
- `packages/visiox-training/src/visiox_training/adapters/paddlex.py`: expose PaddleX profile keys.
- `packages/visiox-training/src/visiox_training/adapters/ultralytics.py`: expose Ultralytics profile keys.
- `packages/visiox-db/src/visiox_db/models/model_space.py`: add per-profile pipeline drafts.
- `apps/api-service/src/visiox_api/main.py`: register framework configuration router.
- `apps/api-service/src/visiox_api/routes/pipelines.py`: serialize and update config drafts safely.
- `apps/api-service/src/visiox_api/services/pipeline_configuration.py`: validate draft/profile identity during pipeline updates.
- `apps/api-service/src/visiox_api/services/training_submission.py`: resolve and freeze native config.
- `apps/api-service/src/visiox_api/routes/training_jobs.py`: use resolved native config during submission/resume.
- `apps/frontend/src/api/client.ts`: configuration API types and methods.
- `apps/frontend/src/views/model-space/ModelSpaceView.vue`: replace legacy editor and form with the focused component.
- `apps/frontend/tests/model-space-view.spec.ts`: wizard integration and legacy migration coverage.
- `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/config.py`: consume validated native PaddleX YAML.
- `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/entrypoint.py`: materialize config file.
- `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/paddlex_main.py`: remove env-only parameter patches covered by native config.
- `workers/training-worker/src/visiox_training_worker/fixed_entrypoint.py`: consume validated Ultralytics native config.
- `tests/unit/test_paddlex_training_config.py`: native YAML and managed-field tests.
- `tests/unit/test_training_worker_package.py`: Ultralytics config-file invocation tests.

## Task 1: Add the Framework Configuration Contracts

**Files:**
- Create: `packages/visiox-training/src/visiox_training/configuration.py`
- Modify: `packages/visiox-training/src/visiox_training/__init__.py`
- Test: `tests/unit/test_framework_configuration.py`

- [ ] **Step 1: Write failing immutable-contract tests**

```python
def test_config_profile_maps_basic_fields_and_locks_managed_paths():
    profile = ConfigProfile(
        profile_key="ultralytics.yolo26n.train.v1",
        framework="ultralytics",
        model_key="yolo26n.pt",
        framework_version="8.3.0",
        template_version="sha256:" + "a" * 64,
        native_template={"epochs": 100, "batch": 8, "data": None},
        recommended_overrides={"epochs": 40},
        basic_fields=(
            ConfigField(name="epochs", path=("epochs",), value_type="integer", minimum=1),
        ),
        managed_paths=(("data",),),
    )
    assert profile.default_config()["epochs"] == 40
    assert profile.field("epochs").path == ("epochs",)
    assert profile.is_managed_path(("data",)) is True


def test_resolve_config_rejects_unknown_and_managed_fields():
    result = validate_user_config(
        profile=_profile(),
        user_config={"epochs": 3, "data": "/tmp/override", "mystery": True},
    )
    assert {error.code for error in result.errors} == {
        "CONFIG_MANAGED_FIELD_OVERRIDE",
        "CONFIG_UNKNOWN_FIELD",
    }
```

- [ ] **Step 2: Run tests and verify contract types are missing**

Run: `python -m pytest tests/unit/test_framework_configuration.py -q`

Expected: FAIL importing `ConfigProfile` from `visiox_training.configuration`.

- [ ] **Step 3: Implement immutable configuration contracts**

Create strict frozen Pydantic models:

```python
class ConfigField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    name: str
    path: tuple[str | int, ...]
    value_type: Literal["string", "integer", "number", "boolean"]
    group: str = "basic"
    label: str
    help_text: str
    default: JsonValue = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[JsonValue, ...] = ()


class ConfigProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    profile_key: str
    framework: str
    model_key: str
    framework_version: str
    template_version: str
    native_template: dict[str, JsonValue]
    recommended_overrides: dict[str, JsonValue]
    basic_fields: tuple[ConfigField, ...]
    managed_paths: tuple[tuple[str | int, ...], ...]
```

Also implement `ConfigValidationError`, `ConfigValidationResult`, `ResolvedNativeConfig`, deep merge/path helpers, canonical JSON serialization and SHA-256 hashing. Validation must reject unknown fields and writes to managed paths without mutating input.

- [ ] **Step 4: Run unit tests**

Run: `python -m pytest tests/unit/test_framework_configuration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the core contract**

```powershell
git add packages/visiox-training/src/visiox_training/configuration.py packages/visiox-training/src/visiox_training/__init__.py tests/unit/test_framework_configuration.py
git commit -m "feat: add native training configuration contracts"
```

## Task 2: Register Model-Specific PaddleX and Ultralytics Profiles

**Files:**
- Create: `packages/visiox-training/src/visiox_training/config_profiles/__init__.py`
- Create: `packages/visiox-training/src/visiox_training/config_profiles/paddlex.py`
- Create: `packages/visiox-training/src/visiox_training/config_profiles/ultralytics.py`
- Modify: `packages/visiox-training/src/visiox_training/adapters/paddlex.py`
- Modify: `packages/visiox-training/src/visiox_training/adapters/ultralytics.py`
- Test: `tests/unit/test_framework_configuration.py`
- Test: `tests/unit/test_framework_capabilities.py`

- [ ] **Step 1: Add failing model-profile tests**

```python
@pytest.mark.parametrize("model_key", ["PP-YOLOE-S", "RT-DETR-L"])
def test_paddlex_profiles_are_model_specific(model_key):
    profile = get_config_profile("paddlex.object_detection.v1", model_key)
    assert profile.framework == "paddlex"
    assert profile.model_key == model_key
    assert profile.field("epochs").path == ("Train", "epochs_iters")
    assert ("Global", "output") in profile.managed_paths


@pytest.mark.parametrize("model_key", ["yolo26n.pt", "yolo26s.pt", "yolo26m.pt", "yolo26l.pt", "yolo26x.pt"])
def test_ultralytics_profiles_use_native_parameter_names(model_key):
    profile = get_config_profile("ultralytics.object_detection.v1", model_key)
    assert profile.field("batch_size").path == ("batch",)
    assert profile.field("learning_rate").path == ("lr0",)
    assert ("data",) in profile.managed_paths
```

- [ ] **Step 2: Verify tests fail without registry**

Run: `python -m pytest tests/unit/test_framework_configuration.py tests/unit/test_framework_capabilities.py -q`

Expected: FAIL because `get_config_profile` and profile identities are missing.

- [ ] **Step 3: Implement pinned templates and registry**

Implement a registry keyed by `(adapter_key, model_key)`. PaddleX profiles must use the exact PP-YOLOE-S and RT-DETR-L template structures pinned by the adapter revision. Ultralytics profiles must start from a checked-in normalized copy of the pinned runtime defaults, not the developer machine's mutable global settings.

Expose only training configuration. Managed paths include:

```python
PADDLEX_MANAGED_PATHS = (
    ("Global", "dataset_dir"),
    ("Global", "output"),
    ("Global", "device"),
    ("Global", "model"),
)

ULTRALYTICS_MANAGED_PATHS = (
    ("task",), ("mode",), ("model",), ("data",), ("project",),
    ("name",), ("exist_ok",), ("device",),
)
```

Register `profile_key` on each model capability so the capability catalog remains the discovery source.

- [ ] **Step 4: Run profile and capability tests**

Run: `python -m pytest tests/unit/test_framework_configuration.py tests/unit/test_framework_capabilities.py -q`

Expected: PASS and each registered model resolves exactly one Profile.

- [ ] **Step 5: Commit profiles**

```powershell
git add packages/visiox-training/src/visiox_training/config_profiles packages/visiox-training/src/visiox_training/adapters tests/unit/test_framework_configuration.py tests/unit/test_framework_capabilities.py
git commit -m "feat: register model native training profiles"
```

## Task 3: Add Profile and Validation APIs

**Files:**
- Create: `apps/api-service/src/visiox_api/routes/framework_configuration.py`
- Create: `apps/api-service/src/visiox_api/services/framework_configuration.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Test: `tests/integration/test_framework_configuration_api.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_get_profile_returns_native_template_and_basic_mapping(client, auth_headers):
    response = client.get(
        "/frameworks/paddlex.object_detection.v1/models/PP-YOLOE-S/config-profile",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["profile_key"] == "paddlex.PP-YOLOE-S.train.v1"
    assert body["basic_fields"][0]["path"]
    assert body["native_template"]["Train"]


def test_validate_config_returns_path_and_line_errors(client, auth_headers):
    response = client.post(
        "/frameworks/ultralytics.object_detection.v1/models/yolo26n.pt/validate-config",
        headers=auth_headers,
        json={"yaml_text": "epochs: nope\ndata: hacked.yaml\n"},
    )
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert {item["code"] for item in response.json()["errors"]} == {
        "CONFIG_INVALID_TYPE", "CONFIG_MANAGED_FIELD_OVERRIDE"
    }
```

- [ ] **Step 2: Run tests and confirm 404**

Run: `python -m pytest tests/integration/test_framework_configuration_api.py -q`

Expected: FAIL with endpoint 404.

- [ ] **Step 3: Implement profile and validation endpoints**

Add authenticated endpoints:

```text
GET  /frameworks/{adapter_key}/models/{model_key}/config-profile
POST /frameworks/{adapter_key}/models/{model_key}/validate-config
```

The service must parse YAML with `yaml.safe_load`, reject aliases that expand beyond safe limits, require a mapping root, return normalized YAML and line-aware errors, and never import PaddleX or Ultralytics runtime libraries in the API process.

- [ ] **Step 4: Run API tests**

Run: `python -m pytest tests/integration/test_framework_configuration_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit API support**

```powershell
git add apps/api-service/src/visiox_api/routes/framework_configuration.py apps/api-service/src/visiox_api/services/framework_configuration.py apps/api-service/src/visiox_api/main.py tests/integration/test_framework_configuration_api.py
git commit -m "feat: expose native framework configuration API"
```

## Task 4: Persist Per-Model Configuration Drafts

**Files:**
- Create: `infra/migrations/versions/20260812_0001_framework_config_drafts.py`
- Modify: `packages/visiox-db/src/visiox_db/models/model_space.py`
- Modify: `apps/api-service/src/visiox_api/routes/pipelines.py`
- Modify: `apps/api-service/src/visiox_api/services/pipeline_configuration.py`
- Modify: `apps/api-service/src/visiox_api/routes/framework_configuration.py`
- Test: `tests/integration/test_migrations.py`
- Test: `tests/integration/test_framework_configuration_api.py`

- [ ] **Step 1: Write failing migration and draft-isolation tests**

```python
def test_pipeline_keeps_independent_profile_drafts(client, auth_headers, pipeline):
    for profile_key, yaml_text in (
        ("paddlex.PP-YOLOE-S.train.v1", "Train:\n  epochs_iters: 3\n"),
        ("ultralytics.yolo26n.train.v1", "epochs: 5\n"),
    ):
        response = client.put(
            f"/pipelines/{pipeline.id}/config-drafts/{profile_key}",
            headers=auth_headers,
            json={"yaml_text": yaml_text},
        )
        assert response.status_code == 200
    assert client.get(
        f"/pipelines/{pipeline.id}/config-drafts/paddlex.PP-YOLOE-S.train.v1",
        headers=auth_headers,
    ).json()["yaml_text"].startswith("Train:")
```

- [ ] **Step 2: Run migration/API tests and verify failure**

Run: `python -m pytest tests/integration/test_migrations.py tests/integration/test_framework_configuration_api.py -q`

Expected: FAIL because `config_drafts` and endpoints do not exist.

- [ ] **Step 3: Add JSON draft storage and authorization-aware routes**

Add `TrainingPipeline.config_drafts` as non-null JSON with `{}` default. Each value stores:

```json
{
  "profile_key": "paddlex.PP-YOLOE-S.train.v1",
  "template_version": "sha256:...",
  "yaml_text": "...",
  "last_valid_config": {},
  "valid": true,
  "errors": [],
  "updated_at": "2026-08-12T00:00:00Z"
}
```

Only users with pipeline edit permission may write drafts. Reading follows pipeline view permission. A draft key must match a registered Profile and the pipeline's task.

- [ ] **Step 4: Run migration and API tests**

Run: `python -m pytest tests/integration/test_migrations.py tests/integration/test_framework_configuration_api.py -q`

Expected: PASS for upgrade, downgrade and draft isolation.

- [ ] **Step 5: Commit persistence**

```powershell
git add infra/migrations/versions/20260812_0001_framework_config_drafts.py packages/visiox-db/src/visiox_db/models/model_space.py apps/api-service/src/visiox_api/routes/pipelines.py apps/api-service/src/visiox_api/services/pipeline_configuration.py apps/api-service/src/visiox_api/routes/framework_configuration.py tests/integration/test_migrations.py tests/integration/test_framework_configuration_api.py
git commit -m "feat: persist model specific configuration drafts"
```

## Task 5: Add Frontend Configuration Types and Draft Helpers

**Files:**
- Modify: `apps/frontend/src/api/client.ts`
- Create: `apps/frontend/src/features/pipeline-wizard/frameworkConfigDraft.ts`
- Test: `apps/frontend/tests/framework-config-draft.spec.ts`

- [ ] **Step 1: Write failing synchronization tests**

```typescript
it("writes a basic field into its native YAML path", () => {
  const next = setConfigPath({ Train: { epochs_iters: 100 } }, ["Train", "epochs_iters"], 3);
  expect(next.Train.epochs_iters).toBe(3);
});

it("does not mutate the previous valid tree when YAML is invalid", () => {
  const result = parseDraftYaml("epochs: [", { epochs: 40 });
  expect(result.valid).toBe(false);
  expect(result.lastValidConfig).toEqual({ epochs: 40 });
});
```

- [ ] **Step 2: Run focused test and verify failure**

Run: `pnpm --dir apps/frontend test -- framework-config-draft.spec.ts`

Expected: FAIL because helper module is missing.

- [ ] **Step 3: Implement client contracts and pure helpers**

Add TypeScript types matching API `ConfigProfile`, `ConfigField`, `ConfigDraft`, `ConfigValidationResult`, and methods for profile get, validate, draft get/save and resolved preview. Implement immutable `getConfigPath`, `setConfigPath`, YAML parse/stringify, modified-path counting and managed-path checks using the existing `yaml` package.

- [ ] **Step 4: Run helper tests and typecheck**

Run: `pnpm --dir apps/frontend test -- framework-config-draft.spec.ts`

Run: `pnpm --dir apps/frontend typecheck`

Expected: PASS.

- [ ] **Step 5: Commit frontend data layer**

```powershell
git add apps/frontend/src/api/client.ts apps/frontend/src/features/pipeline-wizard/frameworkConfigDraft.ts apps/frontend/tests/framework-config-draft.spec.ts
git commit -m "feat: add framework configuration frontend state"
```

## Task 6: Build the Form/YAML Configuration Component

**Files:**
- Create: `apps/frontend/src/features/pipeline-wizard/ObjectDetectionTrainingConfig.vue`
- Test: `apps/frontend/tests/object-detection-training-config.spec.ts`

- [ ] **Step 1: Write failing component behavior tests**

```typescript
it("syncs a form edit into YAML", async () => {
  const wrapper = mount(ObjectDetectionTrainingConfig, { props: profileProps() });
  await wrapper.get('[data-field="epochs"]').setValue("3");
  await wrapper.get('[data-mode="yaml"]').trigger("click");
  expect(wrapper.get("textarea.yaml-editor").element.value).toContain("epochs: 3");
});

it("keeps invalid YAML but blocks progression", async () => {
  const wrapper = mount(ObjectDetectionTrainingConfig, { props: profileProps() });
  await wrapper.get('[data-mode="yaml"]').trigger("click");
  await wrapper.get("textarea.yaml-editor").setValue("epochs: [");
  await vi.advanceTimersByTimeAsync(500);
  expect(wrapper.emitted("validity")?.at(-1)).toEqual([false]);
  expect(wrapper.text()).toContain("YAML 配置无效");
});
```

- [ ] **Step 2: Run component test and verify failure**

Run: `pnpm --dir apps/frontend test -- object-detection-training-config.spec.ts`

Expected: FAIL because component is missing.

- [ ] **Step 3: Implement dual-mode UI**

The component must:

- render profile summary and `表单配置` / `YAML 配置` segmented tabs;
- group fields by Profile group and use input-number, select, checkbox or switch by schema;
- debounce YAML validation by 500ms;
- preserve invalid YAML text while retaining the last valid tree;
- emit `update:draft`, `validity`, `save`, and `reset` events;
- show managed fields in a read-only “平台托管” section;
- require confirmation before a basic-form edit discards invalid YAML;
- expose `validate()` for wizard navigation.

Use stable responsive grid constraints without viewport-font scaling. Do not add a second decorative card around the component.

- [ ] **Step 4: Run component tests and frontend build**

Run: `pnpm --dir apps/frontend test -- object-detection-training-config.spec.ts`

Run: `pnpm --dir apps/frontend build`

Expected: PASS.

- [ ] **Step 5: Commit component**

```powershell
git add apps/frontend/src/features/pipeline-wizard/ObjectDetectionTrainingConfig.vue apps/frontend/tests/object-detection-training-config.spec.ts
git commit -m "feat: add target detection form and YAML configuration"
```

## Task 7: Integrate the Component into the Pipeline Wizard

**Files:**
- Modify: `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- Modify: `apps/frontend/src/features/pipeline-wizard/FrameworkModelSelector.vue`
- Modify: `apps/frontend/tests/model-space-view.spec.ts`
- Modify: `apps/frontend/tests/framework-model-selector.spec.ts`

- [ ] **Step 1: Replace legacy expectations with failing wizard tests**

```typescript
it("shows model selection in data preparation and native config in parameter preparation", async () => {
  const wrapper = mountModelSpaceAtStep(2, paddlexPipeline());
  expect(wrapper.findComponent(ObjectDetectionTrainingConfig).exists()).toBe(true);
  expect(wrapper.find(".legacy-parameter-grid").exists()).toBe(false);
  expect(wrapper.text()).toContain("表单配置");
  expect(wrapper.text()).toContain("YAML 配置");
});

it("does not advance while native config is invalid", async () => {
  const wrapper = mountModelSpaceAtStep(2, paddlexPipeline());
  wrapper.findComponent(ObjectDetectionTrainingConfig).vm.$emit("validity", false);
  await wrapper.get('[data-action="next-step"]').trigger("click");
  expect(wrapper.emitted("step-change")).toBeUndefined();
});
```

- [ ] **Step 2: Run wizard tests and verify old UI fails expectations**

Run: `pnpm --dir apps/frontend test -- model-space-view.spec.ts framework-model-selector.spec.ts`

Expected: FAIL while the handwritten `configMode`, `configText`, YOLO defaults and advanced YAML remain.

- [ ] **Step 3: Wire profile loading, drafts and validity into wizard**

Remove the legacy parameter form/config editor from `ModelSpaceView.vue`. Load Profile after model selection, retrieve the per-profile draft, and mount `ObjectDetectionTrainingConfig` only in parameter preparation. Save on model switch, wizard navigation and explicit save. Keep `FrameworkModelSelector` limited to framework/model selection and remove parameter rendering from it.

For legacy pipelines, construct an in-memory migrated draft from `params_template`; save it only after the user edits or saves.

- [ ] **Step 4: Run wizard tests and full frontend suite**

Run: `pnpm --dir apps/frontend test -- model-space-view.spec.ts framework-model-selector.spec.ts object-detection-training-config.spec.ts`

Run: `pnpm --dir apps/frontend test`

Expected: PASS.

- [ ] **Step 5: Commit wizard integration**

```powershell
git add apps/frontend/src/views/model-space/ModelSpaceView.vue apps/frontend/src/features/pipeline-wizard/FrameworkModelSelector.vue apps/frontend/tests/model-space-view.spec.ts apps/frontend/tests/framework-model-selector.spec.ts
git commit -m "feat: integrate native config into parameter preparation"
```

## Task 8: Resolve and Freeze Native Configuration at Submission

**Files:**
- Modify: `apps/api-service/src/visiox_api/services/training_submission.py`
- Modify: `apps/api-service/src/visiox_api/routes/training_jobs.py`
- Modify: `apps/api-service/src/visiox_api/services/pipeline_configuration.py`
- Test: `tests/integration/test_native_training_configuration.py`
- Test: `tests/integration/test_training_pipeline.py`

- [ ] **Step 1: Write failing snapshot and managed-injection tests**

```python
def test_submission_freezes_resolved_native_config(client, session, configured_pipeline):
    response = submit_job(client, configured_pipeline.id)
    snapshot = response.json()["resolved_snapshot"]
    assert snapshot["config_profile_key"] == "ultralytics.yolo26n.train.v1"
    assert snapshot["resolved_native_config"]["epochs"] == 3
    assert snapshot["resolved_native_config"]["data"].endswith("data.yaml")
    assert snapshot["native_config_sha256"].startswith("sha256:")
    assert snapshot["resolved_native_config"]["device"] == "0"


def test_submission_rejects_invalid_saved_draft_before_job_creation(...):
    response = submit_job(...)
    assert response.status_code == 422
    assert response.json()["code"] == "CONFIG_YAML_SYNTAX_ERROR"
```

- [ ] **Step 2: Run submission tests and verify missing snapshot fields**

Run: `python -m pytest tests/integration/test_native_training_configuration.py tests/integration/test_training_pipeline.py -q`

Expected: FAIL because submissions still merge flat `params_template`.

- [ ] **Step 3: Resolve config server-side in one transaction**

At job creation:

1. load the Profile matching pipeline adapter/model;
2. load the selected valid draft or migrate legacy params;
3. inject dataset/model/output/device/allocation fields;
4. run adapter validation;
5. write `resolved_native_config`, template/profile versions, user changes and SHA-256 into `resolved_snapshot`;
6. place the same normalized native config in `LaunchSpec.inputs.parameters.native_config`;
7. create no job or task if resolution fails.

Resume must copy the original frozen configuration and only replace the managed resume checkpoint path.

- [ ] **Step 4: Run pipeline and submission tests**

Run: `python -m pytest tests/integration/test_native_training_configuration.py tests/integration/test_training_pipeline.py -q`

Expected: PASS.

- [ ] **Step 5: Commit immutable submission**

```powershell
git add apps/api-service/src/visiox_api/services/training_submission.py apps/api-service/src/visiox_api/routes/training_jobs.py apps/api-service/src/visiox_api/services/pipeline_configuration.py tests/integration/test_native_training_configuration.py tests/integration/test_training_pipeline.py
git commit -m "feat: freeze resolved native training configuration"
```

## Task 9: Make the PaddleX Worker Consume Native YAML

**Files:**
- Modify: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/config.py`
- Modify: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/entrypoint.py`
- Modify: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/paddlex_main.py`
- Test: `tests/unit/test_paddlex_training_config.py`
- Test: `tests/unit/test_paddlex_training_worker.py`

- [ ] **Step 1: Write failing native-config materialization tests**

```python
def test_worker_materializes_exact_validated_paddlex_yaml(tmp_path):
    config = build_training_config(_launch_spec(native_config={
        "Train": {"epochs_iters": 2, "batch_size": 4, "learning_rate": 0.0005},
        "Global": {"device": "gpu:0", "output": "/workspace/output"},
    }))
    path = materialize_native_config(config, tmp_path)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["Train"]["epochs_iters"] == 2
    assert loaded["Global"]["output"] == "/workspace/output"


def test_worker_rejects_legacy_unknown_parameter_even_inside_native_config():
    with pytest.raises(ValueError, match="CONFIG_UNKNOWN_FIELD"):
        build_training_config(_launch_spec(native_config={"command": "whoami"}))
```

- [ ] **Step 2: Run PaddleX worker tests and verify failure**

Run: `python -m pytest tests/unit/test_paddlex_training_config.py tests/unit/test_paddlex_training_worker.py -q`

Expected: FAIL because the worker still expects seven flat fields.

- [ ] **Step 3: Materialize validated config and remove redundant patches**

Require `native_config`, profile identity and SHA-256 in LaunchSpec. Verify the hash, write YAML to the attempt work directory, and launch the fixed PaddleX entrypoint with that file. Retain only runtime instrumentation patches such as VisualDL callbacks; remove image-size/workers env patches once their native paths are covered by Profile mapping.

- [ ] **Step 4: Run PaddleX worker tests**

Run: `python -m pytest tests/unit/test_paddlex_training_config.py tests/unit/test_paddlex_training_worker.py -q`

Expected: PASS.

- [ ] **Step 5: Commit PaddleX worker support**

```powershell
git add workers/paddlex-training-worker/src/visiox_paddlex_training_worker tests/unit/test_paddlex_training_config.py tests/unit/test_paddlex_training_worker.py
git commit -m "feat: run PaddleX from resolved native YAML"
```

## Task 10: Make the Ultralytics Worker Consume Native Configuration

**Files:**
- Modify: `workers/training-worker/src/visiox_training_worker/fixed_entrypoint.py`
- Test: `tests/unit/test_training_worker_package.py`
- Test: `tests/unit/test_distributed_training_plan.py`

- [ ] **Step 1: Write failing Ultralytics native-config tests**

```python
def test_fixed_entrypoint_uses_resolved_native_config(monkeypatch):
    payload = launch_spec(native_config={
        "epochs": 2, "batch": 4, "lr0": 0.0005,
        "model": "/workspace/model.pt", "data": "/workspace/data.yaml",
        "project": "/workspace/runs", "name": "job-1", "device": "0",
    })
    run(payload)
    assert captured_train_kwargs == payload["inputs"]["parameters"]["native_config"]


def test_fixed_entrypoint_rejects_hash_mismatch():
    with pytest.raises(ValueError, match="configuration checksum"):
        run(launch_spec(native_config={"epochs": 2}, checksum="sha256:" + "0" * 64))
```

- [ ] **Step 2: Run worker tests and verify old flat path fails**

Run: `python -m pytest tests/unit/test_training_worker_package.py tests/unit/test_distributed_training_plan.py -q`

Expected: FAIL because the worker still reads flat `parameters`.

- [ ] **Step 3: Verify checksum and call Ultralytics with structured kwargs**

The fixed entrypoint must validate the config hash, strip no user fields except fields already fixed by the server, and call the pinned Ultralytics Python API with normalized kwargs. It must not construct a shell command. Keep compatibility reading only for historical attempts that lack `native_config`; new attempts must require it.

- [ ] **Step 4: Run worker tests**

Run: `python -m pytest tests/unit/test_training_worker_package.py tests/unit/test_distributed_training_plan.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Ultralytics worker support**

```powershell
git add workers/training-worker/src/visiox_training_worker/fixed_entrypoint.py tests/unit/test_training_worker_package.py tests/unit/test_distributed_training_plan.py
git commit -m "feat: run Ultralytics from resolved native config"
```

## Task 11: Complete Legacy Migration and Remove Duplicate Parameter Paths

**Files:**
- Modify: `apps/api-service/src/visiox_api/services/framework_configuration.py`
- Modify: `apps/api-service/src/visiox_api/services/pipeline_configuration.py`
- Modify: `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- Modify: `apps/frontend/src/features/pipeline-wizard/FrameworkModelSelector.vue`
- Test: `tests/integration/test_framework_configuration_api.py`
- Test: `apps/frontend/tests/model-space-view.spec.ts`

- [ ] **Step 1: Add failing legacy migration tests**

```python
def test_legacy_ultralytics_params_migrate_to_native_paths():
    migrated = migrate_legacy_params(
        get_config_profile("ultralytics.object_detection.v1", "yolo26n.pt"),
        {"epochs": 3, "batch_size": 4, "learning_rate": 0.0005, "warmup_steps": 5},
    )
    assert migrated.config["batch"] == 4
    assert migrated.config["lr0"] == 0.0005
    assert any(error.path == ("warmup_steps",) for error in migrated.errors)
```

```typescript
it("does not render the old parameter grid or advanced YAML details", () => {
  const wrapper = mountModelSpaceAtStep(2, legacyPipeline());
  expect(wrapper.find(".parameter-grid").exists()).toBe(false);
  expect(wrapper.find("details.advanced-yaml").exists()).toBe(false);
  expect(wrapper.findComponent(ObjectDetectionTrainingConfig).exists()).toBe(true);
});
```

- [ ] **Step 2: Run migration tests and verify failure**

Run: `python -m pytest tests/integration/test_framework_configuration_api.py -q`

Run: `pnpm --dir apps/frontend test -- model-space-view.spec.ts`

Expected: FAIL until all duplicate paths are removed.

- [ ] **Step 3: Implement explicit compatibility mapping and delete dead UI state**

Map only known legacy aliases (`batch_size -> batch`, `learning_rate -> lr0`, PaddleX flat fields to `Train.*`). Preserve unsupported fields as visible migration errors. Delete `configMode`, `configText`, handwritten YOLO defaults/parser and parameter rendering from `FrameworkModelSelector`. Keep historical job detail rendering unchanged.

- [ ] **Step 4: Run API/frontend regression suites**

Run: `python -m pytest tests/integration/test_framework_configuration_api.py tests/integration/test_training_pipeline.py -q`

Run: `pnpm --dir apps/frontend test`

Run: `pnpm --dir apps/frontend build`

Expected: PASS.

- [ ] **Step 5: Commit migration cleanup**

```powershell
git add apps/api-service/src/visiox_api/services/framework_configuration.py apps/api-service/src/visiox_api/services/pipeline_configuration.py apps/frontend/src/views/model-space/ModelSpaceView.vue apps/frontend/src/features/pipeline-wizard/FrameworkModelSelector.vue tests/integration/test_framework_configuration_api.py apps/frontend/tests/model-space-view.spec.ts
git commit -m "refactor: retire duplicate training parameter paths"
```

## Task 12: End-to-End Verification with Real Short Trainings

**Files:**
- Modify if failures reveal contract defects: files from Tasks 1-11 only
- Test: `tests/integration/test_native_training_configuration.py`
- Document evidence: `docs/handoff/2026-08-12-native-training-config-acceptance.md`

- [ ] **Step 1: Run static and automated verification**

Run:

```powershell
python -m pytest tests/unit/test_framework_configuration.py tests/unit/test_framework_capabilities.py tests/unit/test_paddlex_training_config.py tests/unit/test_paddlex_training_worker.py tests/unit/test_training_worker_package.py tests/unit/test_distributed_training_plan.py -q
python -m pytest tests/integration/test_framework_configuration_api.py tests/integration/test_native_training_configuration.py tests/integration/test_training_pipeline.py tests/integration/test_migrations.py -q
pnpm --dir apps/frontend test
pnpm --dir apps/frontend build
```

Expected: all commands PASS.

- [ ] **Step 2: Rebuild affected runtime images**

Run the repository Compose build for API, frontend, Ultralytics training worker and PaddleX training worker using their configured immutable image tags. Record exact image IDs and digests in the acceptance document.

Expected: build succeeds without installing framework packages at job runtime.

- [ ] **Step 3: Run one- or two-epoch Ultralytics acceptance training**

Create a YOLO26n target-detection pipeline using form configuration, then change one advanced YAML-only supported field. Verify:

- resolved snapshot values and SHA-256;
- Worker receives identical native config;
- Ultralytics `args.yaml` reflects epoch, batch, lr0, imgsz and advanced field;
- task reaches success and produces `best.pt` and `last.pt`.

- [ ] **Step 4: Run one- or two-epoch PaddleX acceptance training**

Create a PP-YOLOE-S pipeline using the same validated dataset. Change epochs in the form and a supported scheduler/evaluation field in YAML. Verify:

- resolved snapshot and materialized PaddleX YAML are identical apart from formatting;
- platform dataset/output/device fields override any user attempt;
- PaddleX logs show the configured values;
- task reaches success and registers weights and evaluation artifacts.

- [ ] **Step 5: Verify draft isolation and restart durability**

Switch between PaddleX PP-YOLOE-S, RT-DETR-L and Ultralytics YOLO26n drafts and confirm each restores independently. Restart API/frontend/workers and confirm drafts, frozen snapshots and job details remain available.

- [ ] **Step 6: Write acceptance evidence**

Create `docs/handoff/2026-08-12-native-training-config-acceptance.md` with:

- commands and PASS counts;
- image digests;
- pipeline/job IDs;
- profile/template/config hashes;
- key training log lines;
- produced artifact names;
- any explicit residual limitations.

- [ ] **Step 7: Commit acceptance evidence and final fixes**

```powershell
git add docs/handoff/2026-08-12-native-training-config-acceptance.md
git add packages/visiox-training apps/api-service packages/visiox-db infra/migrations apps/frontend workers tests
git commit -m "test: verify native framework training configuration"
```

## Final Verification Checklist

- [ ] PaddleX and Ultralytics models each resolve exactly one pinned `ConfigProfile`.
- [ ] Form and YAML edit one shared structured configuration.
- [ ] Invalid YAML can be saved as draft but cannot submit.
- [ ] Unknown fields and managed-field overrides fail explicitly.
- [ ] DataLoader workers remain user-configurable within allocated CPU limits.
- [ ] GPU/device, dataset, output and runtime fields remain platform-managed.
- [ ] Each framework/model draft restores independently.
- [ ] Submitted jobs store template version, complete native config and SHA-256.
- [ ] PaddleX and Ultralytics Workers consume the frozen native config.
- [ ] Historical pipelines remain readable through explicit compatibility mapping.
- [ ] Real short trainings prove that both basic and YAML-only settings take effect.
