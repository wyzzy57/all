# Framework Training Form and YAML Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已经验收的多框架训练闭环上，为 PaddleX 与 Ultralytics 目标检测增加模型适配的表单配置和 YAML 配置，并证明配置真实参与训练。

**Architecture:** 复用现有 Framework Capability Catalog、`TrainingPipeline.params_template`、不可变 job snapshot、LaunchSpec 和两个训练 Worker。能力目录补充模型级配置元数据；前端维护一份结构化参数并在表单/YAML之间转换；API 按当前框架和模型校验后继续写入现有参数快照；Worker 只做必要的高级参数透传。不会新增生命周期、调度、可视化、任务状态或独立草稿数据库。

**Tech Stack:** Pydantic v2、FastAPI、Vue 3、TypeScript、Element Plus、`yaml`、Vitest、pytest、PaddleX、Ultralytics

---

## 已有能力，不重复实现

- 三框架适配器与能力目录。
- 任务、框架、模型选择和框架锁定。
- `params_template` 保存配置、job resolved snapshot 和 LaunchSpec。
- PaddleX/Ultralytics 独立训练 Worker 和边缘执行。
- 日志、指标、资源、制品、评估、推理和部署。
- PaddleX PP-YOLOE-S、RT-DETR-L 真实 GPU 生产闭环。

## Task 1: 扩充现有能力目录的模型参数元数据

**Files:**
- Modify: `packages/visiox-training/src/visiox_training/capabilities.py`
- Modify: `packages/visiox-training/src/visiox_training/adapters/paddlex.py`
- Modify: `packages/visiox-training/src/visiox_training/adapters/ultralytics.py`
- Modify: `tests/unit/test_framework_capabilities.py`

- [ ] 给 `ModelCapability` 增加可选的 `config_format`、`config_template`、`basic_parameter_names` 和 `managed_parameter_names`，保持 strict/frozen/deep immutable。
- [ ] 为 PP-YOLOE-S 与 RT-DETR-L 分别声明 PaddleX YAML 模板和模型支持字段；不能让两个模型错误共享一份完整模板。
- [ ] 为 YOLO26 n/s/m/l/x 声明固定 Ultralytics 训练模板；平台托管 `task/mode/model/data/project/name/exist_ok/device`。
- [ ] 保留 `TaskCapability.parameters` 作为 API 校验字段目录，扩展为实际支持的常用和高级训练参数，不新增第二套适配器注册表。
- [ ] 运行 `python -m pytest tests/unit/test_framework_capabilities.py -q`。
- [ ] 仅提交上述文件，提交信息 `feat: publish model training configuration metadata`。

## Task 2: 在现有 API 中校验并规范化 YAML 参数

**Files:**
- Modify: `apps/api-service/src/visiox_api/services/pipeline_configuration.py`
- Modify: `apps/api-service/src/visiox_api/services/training_submission.py`
- Modify: `tests/integration/test_training_pipeline.py`

- [ ] 允许 `params_template` 保存当前模型的结构化配置；不增加配置草稿表或新生命周期。
- [ ] PaddleX YAML 使用明确映射转换为现有 Worker 参数或受控原生 override；Ultralytics YAML 规范化为其原生训练参数名。
- [ ] 拒绝未知参数、类型错误、越界值和平台托管字段覆盖；错误消息包含具体字段。
- [ ] 提交任务时把规范化后的配置继续写入现有 `resolved_snapshot.parameters` 和 LaunchSpec，保留当前不可变快照行为。
- [ ] 历史扁平参数继续可读；只映射已知别名，如 Ultralytics `batch_size -> batch`、`learning_rate -> lr0`。
- [ ] 运行 `python -m pytest tests/integration/test_training_pipeline.py -q`。
- [ ] 提交信息 `feat: validate framework training YAML parameters`。

## Task 3: 实现目标检测表单/YAML双模式组件

**Files:**
- Create: `apps/frontend/src/features/pipeline-wizard/ObjectDetectionTrainingConfig.vue`
- Create: `apps/frontend/src/features/pipeline-wizard/frameworkTrainingConfig.ts`
- Create: `apps/frontend/tests/object-detection-training-config.spec.ts`
- Modify: `apps/frontend/src/api/client.ts`

- [ ] 组件结构与现有大模型参数页一致：`表单配置`、`YAML 配置` 两个页签。
- [ ] 表单根据能力目录字段动态渲染，按基础训练、数据性能、训练稳定性、保存评估分组。
- [ ] YAML 根据当前框架和模型模板生成；表单修改立即更新 YAML，有效 YAML 修改回填表单。
- [ ] YAML 语法、未知字段、托管字段和类型错误就地提示；无效配置不能进入下一步。
- [ ] 模型切换时重建对应模板，避免 PaddleX 和 Ultralytics 参数串用。
- [ ] 使用现有 `yaml` npm 包，不新增重量级编辑器依赖。
- [ ] 运行 `pnpm --dir apps/frontend test -- object-detection-training-config.spec.ts` 和 `pnpm --dir apps/frontend typecheck`。
- [ ] 提交信息 `feat: add detection form and YAML configuration`。

## Task 4: 接入现有参数准备与训练 Worker

**Files:**
- Modify: `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- Modify: `apps/frontend/src/features/pipeline-wizard/FrameworkModelSelector.vue`
- Modify: `apps/frontend/tests/model-space-view.spec.ts`
- Modify: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/config.py`
- Modify only if required: `workers/paddlex-training-worker/src/visiox_paddlex_training_worker/paddlex_main.py`
- Modify: `workers/training-worker/src/visiox_training_worker/fixed_entrypoint.py`
- Modify: `tests/unit/test_paddlex_training_config.py`
- Modify: `tests/unit/test_training_worker_package.py`

- [ ] 删除参数准备中的重复顶部三字段和旧“高级 YAML”入口，换成 Task 3 组件。
- [ ] 继续用现有 `params_template` 保存配置，不改变产线模型选择和 job API。
- [ ] PaddleX Worker 将新增受支持字段映射到 PaddleX 训练配置；不得接受任意命令、路径或环境变量。
- [ ] Ultralytics Worker继续使用结构化 Python kwargs，确保 YAML 支持字段真实传入 `model.train()`。
- [ ] 平台托管字段仍由 API/Worker 覆盖，用户 YAML 不得改变数据集、输出目录和设备分配。
- [ ] 运行目标前端测试、PaddleX Worker 测试和 Ultralytics Worker 测试。
- [ ] 提交信息 `feat: wire framework YAML into existing training workers`。

## Task 5: 回归与真实短训练验收

**Files:**
- Modify only when a verified defect is found: Task 1-4 files
- Create: `docs/handoff/2026-08-12-framework-training-config-acceptance.md`

- [ ] 运行相关后端单元/集成测试与完整前端测试、typecheck、build。
- [ ] 重建受影响的 API、前端和训练 Worker 镜像，不在任务运行时重新安装 Torch/Paddle。
- [ ] 运行一次 1-2 epoch YOLO26n 训练，验证表单参数和至少一个 YAML 高级参数出现在 Ultralytics `args.yaml` 和任务快照。
- [ ] 运行一次 1-2 epoch PP-YOLOE-S 训练，验证表单参数和至少一个 PaddleX 高级参数出现在实际训练配置和日志。
- [ ] 验证非法参数在任务创建前被拒绝，平台托管字段不能被覆盖。
- [ ] 将测试数量、镜像摘要、job ID、关键配置和制品记录到验收文档。
- [ ] 提交信息 `test: verify framework form and YAML training configuration`。

## 完成标准

- PaddleX 和 Ultralytics 参数准备均有表单/YAML两个入口。
- 参数集合与当前模型匹配，不再显示一份跨框架固定配置。
- 两个入口修改同一份现有 `params_template` 状态。
- API 和 Worker 继续使用之前16个任务建立的统一训练闭环。
- 真实短训练证明基本参数和 YAML 高级参数均生效。
