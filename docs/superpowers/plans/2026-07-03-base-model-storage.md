# 对象存储与基础模型下载实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 管理 YOLO26 基础模型源和基础权重状态，并提供首次使用下载、checksum 校验、对象存储写入和 API 查询/触发下载能力。

**架构：** 复用 Task Center 作为下载任务入口，数据库中的 `base_models` 和 `model_sources` 作为状态事实来源。`packages/visiox-storage` 提供对象存储写入抽象和 checksum 工具；`workers/model-worker` 实现可测试的下载流程函数。API 只负责列出基础模型、查询状态和创建 `DOWNLOAD_BASE_MODEL` 任务，不在 HTTP 请求里执行下载。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2.x、httpx、MinIO SDK、Task Center、pytest。

---

## 文件结构

- 修改：`pyproject.toml`，添加 `minio` 依赖，并加入 `packages/visiox-storage/src/visiox_storage` 和 `workers/model-worker/src/visiox_model_worker`。
- 修改：`apps/api-service/src/visiox_api/main.py`，注册 `base_models` router。
- 创建：`packages/visiox-storage/src/visiox_storage/__init__.py`。
- 创建：`packages/visiox-storage/src/visiox_storage/checksum.py`，实现 SHA-256 checksum 工具。
- 创建：`packages/visiox-storage/src/visiox_storage/client.py`，实现对象存储协议、MinIO client wrapper 和测试用内存 storage。
- 创建：`workers/model-worker/src/visiox_model_worker/__init__.py`。
- 创建：`workers/model-worker/src/visiox_model_worker/main.py`，实现基础模型下载任务流程。
- 创建：`apps/api-service/src/visiox_api/routes/base_models.py`，实现基础模型列表、详情和下载任务 API。
- 创建：`tests/integration/test_base_model_download.py`，验证下载任务创建、就绪检查、下载 worker、checksum 错误和 API 行为。

## 任务 1：先写失败的基础模型下载测试

- [ ] **步骤 1：创建 `tests/integration/test_base_model_download.py`**

测试先写，必须在实现前失败。测试使用临时 SQLite + Alembic 创建表，不要求真实 MinIO/Redis/Docker。

测试至少覆盖：

1. `GET /base-models`：
   - 返回基础模型列表。
   - 支持 `task`、`status` 过滤。
2. `GET /base-models/{id}`：
   - 返回单个基础模型状态。
   - 不存在返回 `404`。
3. `POST /base-models/{id}/download`：
   - 当模型不是 `ready` 时，创建 `DOWNLOAD_BASE_MODEL` 任务。
   - 任务持久化到 `tasks` 表。
   - fake stream producer 收到 `TaskCommand`，其中 `task_type=DOWNLOAD_BASE_MODEL`，`resource_refs["base_model_id"]` 等于模型 id。
   - 模型状态变为 `downloading`。
4. `ensure_base_model_ready(session, base_model_id)`：
   - `ready` 时通过并返回模型。
   - 非 `ready` 时抛出明确异常，供 Task 8 训练提交预检查复用。
5. `download_base_model(...)` worker flow：
   - 支持 `local_mount` source，从本地文件复制到 fake object storage。
   - 成功后更新 `BaseModel.local_uri`、`checksum`、`size_bytes`、`status=ready`。
   - 同步更新 Task 状态为 `SUCCESS`。
6. checksum 错误：
   - 如果已有 `BaseModel.checksum` 与下载文件不一致，worker 标记 `BaseModel.status=failed`。
   - Task 标记 `FAILED`，记录错误信息，且不写入 ready。

- [ ] **步骤 2：运行测试并确认失败**

运行：

```bash
pytest tests/integration/test_base_model_download.py -v
```

预期：失败，原因是 storage 包、base model route 和 model worker 尚不存在。

## 任务 2：添加依赖和 package 入口

- [ ] **步骤 1：修改 `pyproject.toml`**

新增依赖：

```toml
  "minio>=7.2,<8.0",
```

新增 wheel package：

```toml
  "packages/visiox-storage/src/visiox_storage",
  "workers/model-worker/src/visiox_model_worker",
```

新增 pytest pythonpath：

```toml
  "packages/visiox-storage/src",
  "workers/model-worker/src",
```

- [ ] **步骤 2：创建包入口**

创建：

- `packages/visiox-storage/src/visiox_storage/__init__.py`
- `workers/model-worker/src/visiox_model_worker/__init__.py`

内容保持简短 docstring。

## 任务 3：实现 checksum 和对象存储抽象

- [ ] **步骤 1：创建 `packages/visiox-storage/src/visiox_storage/checksum.py`**

实现：

- `sha256_file(path: Path) -> str`
- `sha256_bytes(data: bytes) -> str`
- `verify_sha256(actual: str, expected: str | None) -> None`
- mismatch 抛出 `ChecksumMismatchError`，错误消息包含 expected 和 actual。

- [ ] **步骤 2：创建 `packages/visiox-storage/src/visiox_storage/client.py`**

实现：

- `ObjectStorageClient` Protocol：
  - `put_file(bucket: str, object_name: str, path: Path, content_type: str | None = None) -> str`
  - `get_file(bucket: str, object_name: str, destination: Path) -> Path`
- `MinioObjectStorageClient`：
  - 包装 `minio.Minio`。
  - `put_file()` 确保 bucket 存在，再 `fput_object()`。
  - 返回 `minio://{bucket}/{object_name}`。
- `InMemoryObjectStorageClient`：
  - 测试用，把文件 bytes 保存在 dict。
  - 返回 `memory://{bucket}/{object_name}`。

## 任务 4：实现基础模型下载 worker flow

- [ ] **步骤 1：创建 `workers/model-worker/src/visiox_model_worker/main.py`**

实现：

- `BaseModelDownloadError`。
- `DownloadResult` dataclass：
  - `base_model_id`
  - `local_uri`
  - `checksum`
  - `size_bytes`
- `download_base_model(session, task_id, base_model_id, storage, bucket="models") -> DownloadResult`

流程：

1. 查询 `Task`、`BaseModel`、`ModelSource`，不存在则抛错并标记任务失败。
2. 标记任务 `RUNNING`，`stage="download"`。
3. 根据 `ModelSource.type` 获取源文件：
   - `local_mount`：从 `mount_path/source_path` 读取。
   - `http`：用 `httpx.stream("GET", url)` 下载到临时文件。
   - `minio`、`s3`：第一版通过 `storage.get_file(source.bucket, base_model.source_path, tmp_path)` 获取；真实跨存储细节后续扩展。
4. 计算 SHA-256。
5. 如果 `BaseModel.checksum` 已存在，必须校验一致。
6. 写入对象存储：`models/base/{base_model.id}/{filename}`。
7. 更新 `BaseModel.local_uri`、`checksum`、`size_bytes`、`status="ready"`。
8. 标记任务 `SUCCESS`、`progress=100`。
9. 失败时标记 `BaseModel.status="failed"`，Task `FAILED`，记录 `error_code="BASE_MODEL_DOWNLOAD_FAILED"`、`retryable=True`。

要求：

- 不启动长循环 worker；只实现可测试的单任务流程函数。
- 不引入真实 Redis consumer；Task 3 已有 stream consumer helper。

## 任务 5：实现基础模型 API

- [ ] **步骤 1：创建 `apps/api-service/src/visiox_api/routes/base_models.py`**

实现：

- Dependency：
  - `get_base_model_session()` 默认使用 `visiox_db.session.get_session`。
  - `get_stream_producer()` 复用 app lifespan 的 Redis client。
- Response schema：
  - `BaseModelResponse`
  - `BaseModelListResponse`
  - `BaseModelDownloadResponse`
- `GET /base-models`：
  - 支持 `task`、`status`、`limit`、`offset`。
  - 返回 `items`、`total`、`limit`、`offset`。
- `GET /base-models/{base_model_id}`。
- `POST /base-models/{base_model_id}/download`：
  - 如果模型已 `ready`，直接返回状态，不重复创建任务。
  - 否则创建 `Task(task_type=DOWNLOAD_BASE_MODEL, status=QUEUED, resource_type="base_model", resource_id=base_model_id, payload={"base_model_id": base_model_id})`。
  - enqueue `TaskCommand(task_type=DOWNLOAD_BASE_MODEL, resource_refs={"base_model_id": base_model_id})`。
  - 成功后设置 `BaseModel.status="downloading"`。
  - enqueue 抛异常时，任务标记 `FAILED`，模型状态不改成 ready。
- `ensure_base_model_ready(session, base_model_id)`：
  - 模型不存在抛 `HTTPException(404)` 或专用异常。
  - 非 `ready` 抛 `HTTPException(409)` 或专用异常。

- [ ] **步骤 2：修改 `apps/api-service/src/visiox_api/main.py`**

注册 `base_models_router`。

## 任务 6：验证和提交

- [ ] **步骤 1：安装依赖**

运行：

```bash
python -m pip install -e ".[test,dev]"
```

- [ ] **步骤 2：运行基础模型下载测试**

运行：

```bash
pytest tests/integration/test_base_model_download.py -v
```

预期：通过。

- [ ] **步骤 3：运行全量测试**

运行：

```bash
pytest -v
```

预期：通过。

- [ ] **步骤 4：运行 lint**

运行：

```bash
ruff check apps packages tests infra workers
```

预期：通过。

- [ ] **步骤 5：提交**

运行：

```bash
git add pyproject.toml apps/api-service/src/visiox_api packages/visiox-storage workers/model-worker tests/integration/test_base_model_download.py docs/superpowers/plans/2026-07-03-base-model-storage.md
git commit -m "feat: add base model storage"
```

## 自查

- 规格覆盖：本计划覆盖 Task 4 要求的对象存储/checksum、模型源、基础模型 readiness、`DOWNLOAD_BASE_MODEL` 任务 producer、下载 worker flow 和基础模型列表/状态 API。
- 有意延后：不实现训练产线 API、不实现真实长循环 worker daemon、不要求本地真实 MinIO/S3/Redis/Docker、不实现 outbox/reconciler。
- 训练提交阻塞：本任务提供 `ensure_base_model_ready()`，Task 8 训练提交时必须调用。
- 文档语言：本文档正文使用中文；代码、路径、命令、API、状态值和字段名保持英文。
