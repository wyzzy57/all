# Label Studio 集成实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 从平台数据集创建或关联 Label Studio 项目，同步平台样本到 Label Studio，导入 Label Studio 标注结果，并在对象存储保存原始 JSON payload、在数据库保存规范化 annotation。

**架构：** API 服务负责项目生命周期入口和任务提交；`label-sync-worker` 负责实际同步和导入。Label Studio HTTP 访问封装在 `visiox_yolo26.labelstudio.client`，任务专属 labeling config 封装在 `templates.py`。Task 6 只把标注规范化为平台内部 payload，不生成 YOLO 目录、不实现 Ultralytics 数据集转换器。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2.x、httpx、对象存储抽象、Redis Task Center、pytest。

---

## 文件结构

- 修改：`pyproject.toml`，确保 `httpx` 依赖覆盖 API、测试和 worker。
- 修改：`.env.example`，补充 Label Studio token 等配置。
- 修改：`packages/visiox-common/src/visiox_common/settings.py`，增加 Label Studio token、项目同步默认参数。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/labelstudio/__init__.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/labelstudio/client.py`，实现 Label Studio API client。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/labelstudio/templates.py`，生成六类任务的 labeling config。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/labelstudio/importer.py`，把 Label Studio annotation 转为平台内部 annotation payload。
- 创建：`apps/api-service/src/visiox_api/routes/label_projects.py`，实现项目创建/关联/同步/导入 API。
- 修改：`apps/api-service/src/visiox_api/main.py`，注册 label project route。
- 创建：`workers/label-sync-worker/src/visiox_label_sync_worker/__init__.py`。
- 创建：`workers/label-sync-worker/src/visiox_label_sync_worker/main.py`，实现 sync/import command handler。
- 创建：`tests/fixtures/labelstudio/*.json`，放置最小 Label Studio 导出 payload。
- 创建：`tests/integration/test_label_studio_sync.py`，覆盖 API、client、worker 和 importer。

## 任务 1：先写失败的 Label Studio 集成测试

- [ ] **步骤 1：创建 `tests/integration/test_label_studio_sync.py`**

测试必须先写，并在实现前失败。测试使用临时 SQLite + Alembic、`InMemoryObjectStorageClient`、fake Label Studio client 或 `httpx.MockTransport`，不依赖真实 Docker/Label Studio。

测试至少覆盖：

1. Labeling config：
   - `detect` 生成 RectangleLabels 配置。
   - `segment` 和 `semantic` 生成 PolygonLabels/BrushLabels 类配置。
   - `pose` 生成 KeyPointLabels 类配置。
   - `obb` 生成 PolygonLabels 或旋转框可表达配置。
   - `classify` 生成 Choices 配置。
2. API 创建 Label Project：
   - `POST /datasets/{dataset_id}/label-projects` 创建 `LabelProject`。
   - 调用 Label Studio client 创建外部项目。
   - 保存 `provider="label_studio"`、`external_project_id`、`sync_status`。
   - 同 dataset 重复创建返回已有项目或明确 409；计划中选择幂等返回已有项目。
3. API 关联既有项目：
   - 支持请求带 `external_project_id`，不创建外部项目，仅建立平台映射。
4. 样本同步任务：
   - `POST /label-projects/{project_id}/sync-samples` 创建 `SYNC_LABEL_STUDIO_DATA` 任务。
   - 任务 payload 包含 dataset/project 引用。
   - enqueue 失败时 task 记录为失败并保留错误。
5. Worker 样本同步：
   - 从 dataset samples 读取 `file_uri`。
   - 调用 Label Studio import/tasks API 创建任务。
   - 更新 `LabelProject.sync_status` 和 task 状态。
6. Annotation import：
   - 从 Label Studio 导出 payload 读取标注。
   - 原始 JSON 写入对象存储。
   - `annotations.raw_payload_uri` 指向对象存储 URI。
   - `annotations.internal_payload` 写入规范化结构。
   - 更新 sample `annotation_status="labeled"`。
7. 失败可见：
   - client 抛错时 worker 将 Task 标为 `FAILED`，写入 `error_code`/`error_message`，并把项目 `sync_status` 标为失败。

- [ ] **步骤 2：运行测试并确认失败**

运行：

```bash
pytest tests/integration/test_label_studio_sync.py -v
```

预期：失败，原因是 labelstudio package、route 和 worker 尚不存在。

## 任务 2：实现 Label Studio labeling config 模板

- [ ] 创建 `visiox_yolo26.labelstudio.templates`。
- [ ] 定义 `build_label_config(task: str, class_schema: dict) -> str`。
- [ ] 支持任务：`detect`、`segment`、`semantic`、`pose`、`obb`、`classify`。
- [ ] 使用 `class_schema["names"]` 生成标签集合。
- [ ] 对未知 task 抛出清晰错误。
- [ ] 保持输出确定性，便于 fixture 测试。

## 任务 3：实现 Label Studio API client

- [ ] 创建 `LabelStudioClient`。
- [ ] 支持 `create_project(title, label_config)`。
- [ ] 支持 `import_tasks(project_id, tasks)`。
- [ ] 支持 `export_annotations(project_id)`。
- [ ] 支持 `get_project(project_id)` 用于关联校验。
- [ ] 使用 `httpx.Client`，通过 settings 注入 `base_url` 和 token。
- [ ] 对 HTTP 非 2xx 抛出包含状态码和响应摘要的异常。

## 任务 4：实现 API route

- [ ] 创建 `apps/api-service/src/visiox_api/routes/label_projects.py`。
- [ ] `POST /datasets/{dataset_id}/label-projects`：
  - 校验 dataset 存在。
  - 如已存在同 provider 项目，幂等返回。
  - 未传 `external_project_id` 时调用 Label Studio 创建项目。
  - 传入 `external_project_id` 时只关联。
- [ ] `GET /datasets/{dataset_id}/label-projects`：列出项目。
- [ ] `POST /label-projects/{project_id}/sync-samples`：
  - 创建 `SYNC_LABEL_STUDIO_DATA` task。
  - enqueue 到 `stream:label_sync.commands`。
- [ ] `POST /label-projects/{project_id}/import-annotations`：
  - 创建 `IMPORT_LABEL_STUDIO_ANNOTATION` task。
  - enqueue 到 `stream:label_sync.commands`。
- [ ] enqueue 失败时沿用 Task Center 现有失败记录模式。

## 任务 5：实现 Label Sync worker

- [ ] 创建 `workers/label-sync-worker/src/visiox_label_sync_worker/main.py`。
- [ ] 实现 `sync_label_project_samples(session, storage, client, task_id, label_project_id)`：
  - 读取 LabelProject、Dataset、DatasetSample。
  - 把样本转成 Label Studio task payload。
  - 调用 client import。
  - 更新 task 成功/失败状态。
- [ ] 实现 `import_label_project_annotations(session, storage, client, task_id, label_project_id)`：
  - 调用 client export。
  - 每个 sample 的原始 payload 写入对象存储。
  - 写入或更新 Annotation。
  - 更新 sample annotation status。
- [ ] 第一版 worker 可暴露纯函数，不强制实现长驻 Redis consumer；Redis consumer 后续可按任务中心模式接入。

## 任务 6：实现 Annotation importer

- [ ] 创建 `visiox_yolo26.labelstudio.importer`。
- [ ] 将 Label Studio result 规范化为内部 payload：
  - `class_name` 或 `class_id`。
  - `shape` 类型：rectangle、polygon、brush、keypoints、classification。
  - 坐标保留 Label Studio 百分比值，Task 7 再转 YOLO。
- [ ] 对空标注、未知 result 类型、缺失 sample 映射返回可操作错误或 warning。
- [ ] 保留原始 result id，便于追溯。

## 任务 7：验证与提交

- [ ] 运行：

```bash
pytest tests/integration/test_label_studio_sync.py -v
pytest -v
ruff check apps packages tests infra workers
```

- [ ] 若 Docker 可用，再运行 Compose smoke；当前环境不可用时在最终说明里明确。
- [ ] 提交为 `feat: integrate label studio`。

## 验收标准

- 数据集可以创建或关联 Label Studio 项目。
- 平台样本可以同步为 Label Studio task payload。
- Label Studio 标注可以导入平台，原始 payload 存在对象存储，规范化 annotation 存在数据库。
- 同步和导入失败在 Task Center 可见。
- 不实现 YOLO26 数据集导出目录；该范围留给 Task 7。
