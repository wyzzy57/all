# Visiox MVP 实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 构建第一版私有化部署的 Visiox MVP，支持 YOLO26 模型空间、数据准备、Label Studio 同步、训练产线、任务中心、边缘应用打包、Edge Agent 部署和基础 Web 管理。

**架构：** 使用 Docker Compose monorepo，包含 FastAPI API 服务、Python worker、PostgreSQL、Redis Stream/PubSub、MinIO、Registry、Label Studio、Vue 3 前端、Edge Agent 和统一 YOLO26 推理镜像。按纵向切片实施，保证每个子系统在集成前可测试。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2.x、Alembic、PostgreSQL、Redis Streams、MinIO SDK、Docker Compose、Vue 3、TypeScript、Vite、Element Plus、Pinia、Vitest、pytest。

---

## 范围检查

架构规格覆盖多个相互独立的子系统。如果作为一个巨大计划一次性实施，会导致边界薄弱、评审点不足。本文档是实施计划索引和执行顺序。下面每个任务都是子实施计划，在编码开始前必须展开为代码级计划。

相关子计划存在之前，不要开始功能实现。

## 目标仓库结构

```text
visiox/
├── apps/
│   ├── api-service/
│   ├── frontend/
│   ├── edge-agent/
│   └── yolo26-inference/
├── workers/
│   ├── training-worker/
│   ├── deployment-worker/
│   ├── label-sync-worker/
│   └── camera-worker/
├── packages/
│   ├── visiox-common/
│   ├── visiox-db/
│   ├── visiox-messaging/
│   ├── visiox-storage/
│   └── visiox-yolo26/
├── infra/
│   ├── compose/
│   ├── migrations/
│   └── seed/
├── tests/
│   ├── integration/
│   └── fixtures/
└── docs/
    └── superpowers/
```

职责：

- `apps/api-service`：FastAPI HTTP API、OpenAPI、WebSocket 任务进度、业务入口。
- `apps/frontend`：Vue 3 管理台。
- `apps/edge-agent`：设备侧 Docker/本机进程生命周期控制 API。
- `apps/yolo26-inference`：统一 YOLO26 推理服务镜像。
- `workers/*`：长时间运行的异步任务消费者。
- `packages/visiox-common`：共享枚举、schema、settings、errors。
- `packages/visiox-db`：SQLAlchemy model、session 管理、repository helper。
- `packages/visiox-messaging`：Redis Stream/PubSub 抽象和消息契约。
- `packages/visiox-storage`：MinIO/object storage 和本地挂载抽象。
- `packages/visiox-yolo26`：YOLO26 任务注册表、数据集转换器、train/export 命令构建器。
- `infra/compose`：Docker Compose 文件和 env 模板。
- `infra/migrations`：Alembic migration 环境。
- `infra/seed`：初始 YOLO26 基础模型元数据。
- `tests/integration`：跨服务和 Compose 级测试。
- `tests/fixtures`：样例数据集、Label Studio payload 和预期转换输出。

## 执行顺序

### 任务 1：仓库基础与 Compose 骨架

**文件：**
- 创建：`pyproject.toml`
- 创建：`package.json`
- 创建：`.env.example`
- 创建：`infra/compose/docker-compose.yml`
- 创建：`infra/compose/docker-compose.dev.yml`
- 创建：`apps/api-service/Dockerfile`
- 创建：`apps/api-service/src/visiox_api/main.py`
- 创建：`packages/visiox-common/src/visiox_common/settings.py`
- 创建：`tests/integration/test_api_health.py`
- 修改：`.gitignore`

**目标：** 通过 Docker Compose 运行一个包含 Postgres、Redis、MinIO、Registry 和 Label Studio 的最小 API。

- [ ] 在 `docs/superpowers/plans/2026-07-03-repo-foundation-compose.md` 编写子计划。
- [ ] 添加最小 Python packaging 和 lint/test 命令。
- [ ] 为 `api-service` 添加 `GET /health`。
- [ ] 添加 `api-service`、`postgres`、`redis`、`minio`、`registry` 和 `label-studio` 的 Compose 服务。
- [ ] 为 `GET /health` 添加集成测试。
- [ ] 使用 `docker compose -f infra/compose/docker-compose.yml up --build` 验证。
- [ ] 提交为 `chore: scaffold platform foundation`。

**验收标准：**
- `pytest tests/integration/test_api_health.py -v` 通过。
- `GET /health` 返回服务状态和依赖配置，不要求业务表存在。
- Compose 能启动所有基础设施容器。

### 任务 2：数据库核心、迁移和种子数据

**文件：**
- 创建：`packages/visiox-db/src/visiox_db/base.py`
- 创建：`packages/visiox-db/src/visiox_db/session.py`
- 创建：`packages/visiox-db/src/visiox_db/models/*.py`
- 创建：`infra/migrations/env.py`
- 创建：`infra/seed/yolo26_base_models.json`
- 创建：`tests/integration/test_migrations.py`

**目标：** 为任务中心、模型空间、数据准备、设备、摄像头、边缘应用和部署记录建立 PostgreSQL schema。

- [ ] 在 `docs/superpowers/plans/2026-07-03-database-core.md` 编写子计划。
- [ ] 为 `Task`、`ModelSource`、`BaseModel`、`TrainedModel`、`TrainingPipeline`、`TrainingJob`、`Dataset`、`DatasetSample`、`Annotation`、`LabelProject`、`Device`、`Camera`、`EdgeApp`、`EdgeAppVersion` 和 `Deployment` 定义 SQLAlchemy model。
- [ ] 添加 Alembic migration 环境。
- [ ] 写入 30 条 YOLO26 基础模型元数据种子记录。
- [ ] 测试空数据库上的 migration upgrade。
- [ ] 提交为 `feat: add database core schema`。

**验收标准：**
- Alembic upgrade 能创建全部表。
- 种子数据精确生成 30 条 YOLO26 基础模型记录：6 类任务乘以 5 个尺度。
- 不存在用户、角色或权限表。

### 任务 3：Redis 消息与任务中心

**文件：**
- 创建：`packages/visiox-messaging/src/visiox_messaging/streams.py`
- 创建：`packages/visiox-messaging/src/visiox_messaging/pubsub.py`
- 创建：`packages/visiox-common/src/visiox_common/tasks.py`
- 创建：`apps/api-service/src/visiox_api/routes/tasks.py`
- 创建：`apps/api-service/src/visiox_api/ws/tasks.py`
- 创建：`tests/integration/test_task_center.py`

**目标：** 所有长任务使用统一任务中心和 Redis 消息契约。

- [ ] 在 `docs/superpowers/plans/2026-07-03-task-center-messaging.md` 编写子计划。
- [ ] 实现任务状态模型：`PENDING`、`QUEUED`、`RUNNING`、`SUCCESS`、`FAILED`、`CANCELED`。
- [ ] 实现 Redis Stream producer 和 consumer helper。
- [ ] 实现 Pub/Sub 进度事件发布器。
- [ ] 添加创建、列表、详情和取消任务的 API endpoint。
- [ ] 添加任务进度 WebSocket endpoint。
- [ ] 测试任务创建、stream 入队、状态更新和进度事件传播。
- [ ] 提交为 `feat: add task center messaging`。

**验收标准：**
- 每个任务都有持久化数据库状态。
- Redis Stream 消息包含 task id、task type、resource refs 和 payload version。
- Redis 丢失不会抹掉最终任务状态。

### 任务 4：对象存储与基础模型下载

**文件：**
- 创建：`packages/visiox-storage/src/visiox_storage/client.py`
- 创建：`packages/visiox-storage/src/visiox_storage/checksum.py`
- 创建：`workers/model-worker/src/visiox_model_worker/main.py`
- 创建：`apps/api-service/src/visiox_api/routes/base_models.py`
- 创建：`tests/integration/test_base_model_download.py`

**目标：** 管理模型源，并在首次使用时从内部文件服务器把 YOLO26 基础权重下载到对象存储。

- [ ] 在 `docs/superpowers/plans/2026-07-03-base-model-storage.md` 编写子计划。
- [ ] 实现可配置模型源：`http`、`minio`、`s3`、`local_mount`。
- [ ] 实现基础模型就绪检查。
- [ ] 添加 `DOWNLOAD_BASE_MODEL` 任务 producer。
- [ ] 实现带 checksum 校验的下载 worker。
- [ ] 暴露基础模型列表和状态 API。
- [ ] 提交为 `feat: add base model storage`。

**验收标准：**
- 创建产线可以引用远程基础模型。
- 训练提交必须等选中的基础模型为 `ready` 后才允许。
- 下载失败会在任务中心记录错误详情。

### 任务 5：数据准备模块

**文件：**
- 创建：`apps/api-service/src/visiox_api/routes/datasets.py`
- 创建：`apps/api-service/src/visiox_api/routes/dataset_samples.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/datasets/analysis.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/datasets/validation.py`
- 创建：`tests/fixtures/datasets/*`
- 创建：`tests/integration/test_dataset_upload.py`

**目标：** 上传数据、创建数据集、管理样本、分析分布，并在训练前校验数据。

- [ ] 在 `docs/superpowers/plans/2026-07-03-data-preparation.md` 编写子计划。
- [ ] 实现图片和 zip 上传到对象存储。
- [ ] 创建 dataset 和 sample 记录。
- [ ] 存储图片尺寸、checksum、split 和 annotation status。
- [ ] 实现 train/val/test 划分分配。
- [ ] 实现分析：sample count、class distribution、annotation count、image size distribution、empty annotation ratio、invalid samples。
- [ ] 按任务类型实现格式校验 hook。
- [ ] 提交为 `feat: add data preparation module`。

**验收标准：**
- 用户可以不依赖 Label Studio 创建数据集。
- 上传样本存储在 MinIO，并在 PostgreSQL 中建索引。
- 数据集分析作为任务中心任务运行。

### 任务 6：Label Studio 集成

**文件：**
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/labelstudio/client.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/labelstudio/templates.py`
- 创建：`workers/label-sync-worker/src/visiox_label_sync_worker/main.py`
- 创建：`apps/api-service/src/visiox_api/routes/label_projects.py`
- 创建：`tests/fixtures/labelstudio/*.json`
- 创建：`tests/integration/test_label_studio_sync.py`

**目标：** 从平台数据集创建 Label Studio 项目，同步样本，导入标注，并存储原始标注和规范化标注数据。

- [ ] 在 `docs/superpowers/plans/2026-07-03-label-studio-integration.md` 编写子计划。
- [ ] 实现 Label Studio API client。
- [ ] 为 detect、segment、semantic、pose、obb 和 classify 生成任务专属 Label Studio config。
- [ ] 实现从平台数据集到 Label Studio 项目的样本同步。
- [ ] 实现从 Label Studio 到平台的 annotation import。
- [ ] 在对象存储中保存原始 Label Studio JSON payload，并在数据库中保存规范化 annotation。
- [ ] 提交为 `feat: integrate label studio`。

**验收标准：**
- 数据集可以创建或关联 Label Studio 项目。
- Label Studio 标注可以同步回平台记录。
- 同步失败在任务中心可见。

### 任务 7：YOLO26 数据集转换器

**文件：**
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/tasks.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/internal_schema.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/detect.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/segment.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/semantic.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/pose.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/obb.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/classify.py`
- 创建：`tests/fixtures/yolo26_expected/*`
- 创建：`tests/integration/test_yolo26_converters.py`

**目标：** 将内部 annotation 转换为全部六类任务的有效 Ultralytics YOLO26 数据集格式。

- [ ] 在 `docs/superpowers/plans/2026-07-03-yolo26-dataset-converters.md` 编写子计划。
- [ ] 实现六类 YOLO26 任务和五个尺度的任务注册表。
- [ ] 实现 detect 转换器。
- [ ] 实现实例分割转换器。
- [ ] 实现从 polygon/brush 到语义分割 mask 的 rasterization。
- [ ] 实现 COCO 17 点 pose 转换器。
- [ ] 实现从旋转矩形或四点多边形到归一化四点 label 的 OBB 转换器。
- [ ] 实现 classification 转换器。
- [ ] 为每类任务生成 `data.yaml`。
- [ ] 提交为 `feat: add yolo26 dataset converters`。

**验收标准：**
- 确定性 fixture 转换输出逐字节匹配预期文件。
- 无效 annotation 产生可操作的 validation error。
- 语义重叠按确定性覆盖顺序产生警告。

### 任务 8：训练产线与训练 Worker

**文件：**
- 创建：`apps/api-service/src/visiox_api/routes/pipelines.py`
- 创建：`apps/api-service/src/visiox_api/routes/training_jobs.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/training/params.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/training/commands.py`
- 创建：`workers/training-worker/src/visiox_training_worker/main.py`
- 创建：`tests/integration/test_training_pipeline.py`

**目标：** 创建训练产线、校验参数、提交训练任务、运行 YOLO26 train/val/export 命令，并登记训练模型版本。

- [ ] 在 `docs/superpowers/plans/2026-07-03-training-pipeline-worker.md` 编写子计划。
- [ ] 实现 pipeline CRUD API。
- [ ] 实现参数校验和高级配置白名单。
- [ ] 实现训练环境注册表。
- [ ] 实现 `TRAIN_MODEL` worker flow。
- [ ] 实现训练日志采集和指标登记。
- [ ] 实现训练模型版本创建。
- [ ] 提交为 `feat: add training pipeline worker`。

**验收标准：**
- 基础模型、数据集、参数和环境未通过预检查时不能开始训练。
- 训练成功后创建训练模型版本。
- 失败时记录 stage、error code 和 retry flag。

### 任务 9：模型导出与边缘应用打包

**文件：**
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/export/commands.py`
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/edge_app/package.py`
- 创建：`apps/api-service/src/visiox_api/routes/edge_apps.py`
- 创建：`workers/training-worker/src/visiox_training_worker/export_flow.py`
- 创建：`tests/integration/test_edge_app_package.py`

**目标：** 导出训练后的 YOLO26 模型，并构建包含模型、runtime config、camera config、rules 和 image references 的边缘应用包。

- [ ] 在 `docs/superpowers/plans/2026-07-03-model-export-edge-app-package.md` 编写子计划。
- [ ] 实现模型导出任务。
- [ ] 实现 edge app 和 edge app version 记录。
- [ ] 实现 package manifest `app.yaml`。
- [ ] 将模型、runtime config、camera config 和 rules 打包进对象存储。
- [ ] 提交为 `feat: add edge app packaging`。

**验收标准：**
- 训练模型可以生成边缘应用版本。
- Package manifest 包含 task、model URI、image ref、cameras、rules 和 checksum。
- 部署 worker 可以下载 package。

### 任务 10：Edge Agent 与部署 Worker

**文件：**
- 创建：`apps/edge-agent/src/visiox_edge_agent/main.py`
- 创建：`apps/edge-agent/src/visiox_edge_agent/docker_runtime.py`
- 创建：`apps/edge-agent/src/visiox_edge_agent/apps.py`
- 创建：`workers/deployment-worker/src/visiox_deployment_worker/main.py`
- 创建：`apps/api-service/src/visiox_api/routes/devices.py`
- 创建：`apps/api-service/src/visiox_api/routes/deployments.py`
- 创建：`tests/integration/test_deployment_flow.py`

**目标：** 注册边缘设备，调用 Edge Agent API，部署、停止、回滚边缘应用，并跟踪部署状态。

- [ ] 在 `docs/superpowers/plans/2026-07-03-edge-agent-deployment.md` 编写子计划。
- [ ] 实现 Edge Agent health 和 device info API。
- [ ] 实现 Agent app deploy/start/stop/rollback API。
- [ ] 实现部署 worker 对 Agent 的调用。
- [ ] 实现部署任务状态和日志。
- [ ] 实现通过 Agent 代理的 camera test API。
- [ ] 提交为 `feat: add edge deployment flow`。

**验收标准：**
- 平台可以向测试 Agent 部署边缘应用。
- 部署失败可恢复，并在任务中心可见。
- Agent 不包含 YOLO 推理逻辑。

### 任务 11：统一 YOLO26 推理服务

**文件：**
- 创建：`apps/yolo26-inference/Dockerfile`
- 创建：`apps/yolo26-inference/src/visiox_yolo26_inference/main.py`
- 创建：`apps/yolo26-inference/src/visiox_yolo26_inference/config.py`
- 创建：`apps/yolo26-inference/src/visiox_yolo26_inference/predict.py`
- 创建：`tests/integration/test_yolo26_inference_api.py`

**目标：** 提供一个推理服务镜像，通过 runtime config 加载不同 YOLO26 任务模型。

- [ ] 在 `docs/superpowers/plans/2026-07-03-yolo26-inference-service.md` 编写子计划。
- [ ] 实现 `GET /health`。
- [ ] 实现 `GET /model/info`。
- [ ] 实现 `POST /predict/image`。
- [ ] 实现 `POST /predict/video-frame`。
- [ ] 实现 `POST /runtime/reload`。
- [ ] 实现 `GET /metrics`。
- [ ] 提交为 `feat: add yolo26 inference service`。

**验收标准：**
- 同一个镜像可以加载 detect、segment、semantic、pose、obb 和 classify 的任务配置。
- model/task 不匹配时快速失败，并返回清晰错误。

### 任务 12：前端管理台

**文件：**
- 创建：`apps/frontend/package.json`
- 创建：`apps/frontend/src/main.ts`
- 创建：`apps/frontend/src/router/index.ts`
- 创建：`apps/frontend/src/stores/taskCenter.ts`
- 创建：`apps/frontend/src/views/model-space/*`
- 创建：`apps/frontend/src/views/data-preparation/*`
- 创建：`apps/frontend/src/views/pipelines/*`
- 创建：`apps/frontend/src/views/tasks/*`
- 创建：`apps/frontend/src/views/devices/*`
- 创建：`apps/frontend/src/views/edge-apps/*`
- 创建：`apps/frontend/src/api/client.ts`
- 创建：`apps/frontend/tests/*.spec.ts`

**目标：** 构建模型空间、数据准备、产线向导、任务中心、设备、边缘应用和部署的管理 UI。

- [ ] 在 `docs/superpowers/plans/2026-07-03-frontend-console.md` 编写子计划。
- [ ] 初始化 Vue 3 + TypeScript + Vite + Element Plus。
- [ ] 从 FastAPI OpenAPI 生成或实现 API client。
- [ ] 实现导航和布局。
- [ ] 实现模型空间页面。
- [ ] 实现数据准备页面。
- [ ] 实现四步训练产线向导。
- [ ] 实现带实时进度的任务中心列表/详情。
- [ ] 实现设备、摄像头、边缘应用和部署页面。
- [ ] 提交为 `feat: add frontend console`。

**验收标准：**
- 用户可以从 UI 经 API 完成 MVP 工作流。
- 长任务显示状态和错误详情。
- 不存在登录页或基于角色的 UI。

### 任务 13：端到端 MVP 验证

**文件：**
- 创建：`tests/integration/test_mvp_yolo26_detect_flow.py`
- 创建：`tests/integration/test_mvp_labelstudio_flow.py`
- 创建：`docs/runbooks/local-mvp.md`
- 修改：`README.md`

**目标：** 验证从数据集上传到训练任务、模型版本、边缘应用包，以及部署到测试 Edge Agent 的完整工作流。

- [ ] 在 `docs/superpowers/plans/2026-07-03-mvp-e2e-verification.md` 编写子计划。
- [ ] 添加本地 MVP runbook。
- [ ] 为至少一个 detect 工作流添加确定性测试 fixture。
- [ ] 添加覆盖数据集上传、转换、训练任务提交、模型登记、package 构建和部署调用的集成测试。
- [ ] 添加 Label Studio 项目创建和 annotation sync 的 smoke test。
- [ ] 提交为 `test: add mvp end-to-end verification`。

**验收标准：**
- 开发者可以在干净机器上按 `docs/runbooks/local-mvp.md` 操作。
- 端到端测试在本地 Compose 下通过。
- 已知限制均已文档化。

## 提交策略

每个任务或小任务子集使用一个提交。除非它们属于同一个纵向切片所必需，不要在同一提交中混合前端、worker 和数据库变更。每个提交都必须让所触达子系统的测试保持通过。

## 自查

规格覆盖：

- 部署形态：由任务 1 和任务 13 覆盖。
- 数据库和元数据对象：由任务 2 覆盖。
- Redis 和任务中心：由任务 3 覆盖。
- 基础模型下载：由任务 4 覆盖。
- 数据准备模块：由任务 5 覆盖。
- Label Studio 同步：由任务 6 覆盖。
- YOLO26 六类任务转换：由任务 7 覆盖。
- 训练产线和 worker：由任务 8 覆盖。
- 导出和边缘应用包：由任务 9 覆盖。
- Edge Agent 和部署：由任务 10 覆盖。
- 统一 YOLO26 推理镜像：由任务 11 覆盖。
- 前端管理台：由任务 12 覆盖。
- MVP 验证：由任务 13 覆盖。

已知有意延后：

- 每个任务必须在编码前展开为各自的代码级实施计划。这是必要要求，因为当前规格跨越多个独立子系统。
