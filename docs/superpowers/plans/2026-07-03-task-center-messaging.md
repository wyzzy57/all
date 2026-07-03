# Redis 消息与任务中心实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 建立 Visiox 第一版 Task Center 和 Redis Stream/PubSub 消息契约，让长任务拥有持久化数据库状态、可靠队列消息和实时进度通知入口。

**架构：** Task Center 以 `tasks` 数据库表作为事实来源；API 负责创建、查询和取消任务，并把长任务命令写入 Redis Stream。`packages/visiox-messaging` 只封装消息格式和 Redis client 适配，不直接依赖 FastAPI。实时通知通过 Pub/Sub 抽象注入到 WebSocket endpoint，测试中使用内存 fake，后续 worker 再接真实 Redis。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2.x、Redis asyncio client、Pydantic、pytest、FastAPI TestClient。

---

## 文件结构

- 修改：`pyproject.toml`，添加 `redis` 依赖，并加入 `packages/visiox-messaging/src/visiox_messaging`。
- 创建：`packages/visiox-common/src/visiox_common/tasks.py`，定义任务状态、任务类型、任务消息 schema 和进度事件 schema。
- 创建：`packages/visiox-messaging/src/visiox_messaging/__init__.py`。
- 创建：`packages/visiox-messaging/src/visiox_messaging/streams.py`，实现 Redis Stream producer/consumer helper。
- 创建：`packages/visiox-messaging/src/visiox_messaging/pubsub.py`，实现 Pub/Sub progress publisher 和订阅接口。
- 创建：`apps/api-service/src/visiox_api/routes/__init__.py`。
- 创建：`apps/api-service/src/visiox_api/routes/tasks.py`，实现任务创建、列表、详情、取消 API。
- 创建：`apps/api-service/src/visiox_api/ws/__init__.py`。
- 创建：`apps/api-service/src/visiox_api/ws/tasks.py`，实现任务进度 WebSocket endpoint。
- 修改：`apps/api-service/src/visiox_api/main.py`，注册 routes 和 websocket。
- 创建：`tests/integration/test_task_center.py`，验证任务创建、stream 入队、状态更新和进度事件传播。

## 任务 1：先写失败的 Task Center 集成测试

- [ ] **步骤 1：创建 `tests/integration/test_task_center.py`**

测试先写，必须在实现前失败。测试设计：

- 使用 SQLite 临时库，通过 Alembic upgrade 创建 `tasks` 表。
- override API 的 DB session dependency。
- 使用 fake stream producer 捕获 Redis Stream 消息。
- 使用 fake progress broker 注入 WebSocket 事件。

测试至少覆盖：

1. `POST /tasks` 创建任务：
   - 返回 `201`。
   - 数据库中任务状态为 `QUEUED`。
   - fake stream 收到一条消息，包含 `task_id`、`task_type`、`resource_refs`、`payload_version`。
2. `GET /tasks` 和 `GET /tasks/{id}`：
   - 能读取持久化任务。
3. `POST /tasks/{id}/cancel`：
   - `QUEUED` 任务变为 `CANCELED`。
   - 已结束任务不能取消并返回 `409`。
4. `PATCH` 或内部 helper 更新任务状态：
   - 用代码级 helper 把任务更新为 `RUNNING` 并写入 progress。
5. `WebSocket /ws/tasks/{id}`：
   - fake broker 发布 progress event 后，WebSocket 客户端收到对应 JSON。

- [ ] **步骤 2：运行测试并确认失败**

运行：

```bash
pytest tests/integration/test_task_center.py -v
```

预期：失败，原因是 `visiox_common.tasks`、`visiox_messaging`、API routes 和 WebSocket 尚不存在。

## 任务 2：定义共享任务契约

- [ ] **步骤 1：创建 `packages/visiox-common/src/visiox_common/tasks.py`**

定义：

- `TaskStatus(StrEnum)`：
  - `PENDING`
  - `QUEUED`
  - `RUNNING`
  - `SUCCESS`
  - `FAILED`
  - `CANCELED`
- `TaskType(StrEnum)`：
  - `DOWNLOAD_BASE_MODEL`
  - `SYNC_LABEL_STUDIO_DATA`
  - `IMPORT_LABEL_STUDIO_ANNOTATION`
  - `VALIDATE_DATASET_FORMAT`
  - `TRAIN_MODEL`
  - `CONVERT_MODEL`
  - `BUILD_EDGE_APP_PACKAGE`
  - `DEPLOY_APP`
  - `ROLLBACK_APP`
  - `STOP_APP`
  - `CAPTURE_CAMERA_SAMPLE`
  - `TEST_CAMERA_CONNECTION`
- `TaskCommand` Pydantic model：
  - `task_id: str`
  - `task_type: TaskType`
  - `resource_refs: dict[str, str] = {}`
  - `payload: dict[str, Any] = {}`
  - `payload_version: int = 1`
- `TaskProgressEvent` Pydantic model：
  - `task_id: str`
  - `status: TaskStatus`
  - `progress: int`
  - `stage: str | None = None`
  - `message: str | None = None`
  - `error_code: str | None = None`
  - `error_message: str | None = None`

要求：

- `progress` 必须限制在 `0..100`。
- `TaskCommand` 能序列化为 Redis Stream 友好的 `dict[str, str]`。
- `TaskProgressEvent` 能输出 WebSocket JSON。

## 任务 3：实现 Redis Stream 和 Pub/Sub 抽象

- [ ] **步骤 1：修改 `pyproject.toml`**

新增依赖：

```toml
  "redis>=5.0,<6.0",
```

新增 wheel package：

```toml
  "packages/visiox-messaging/src/visiox_messaging",
```

新增 pytest pythonpath：

```toml
  "packages/visiox-messaging/src",
```

- [ ] **步骤 2：创建 `packages/visiox-messaging/src/visiox_messaging/streams.py`**

实现：

- `STREAM_BY_TASK_TYPE: dict[TaskType, str]`，至少映射：
  - 训练相关 -> `stream:training.commands`
  - 部署相关 -> `stream:deployment.commands`
  - Label Studio 同步相关 -> `stream:label_sync.commands`
  - 摄像头相关 -> `stream:camera.commands`
- `RedisStreamProducer`：
  - `enqueue(command: TaskCommand) -> str`
  - 使用 `xadd(stream_name, command.to_stream_fields())`
- `RedisStreamConsumer`：
  - `read_group(stream_name, group_name, consumer_name, count=10, block_ms=1000)`
  - `ack(stream_name, group_name, message_id)`
- 所有 helper 接收注入的 redis client，测试可用 fake client。

- [ ] **步骤 3：创建 `packages/visiox-messaging/src/visiox_messaging/pubsub.py`**

实现：

- 常量 `TASK_PROGRESS_CHANNEL = "pubsub:task.progress"`。
- `TaskProgressPublisher.publish(event: TaskProgressEvent) -> int`。
- `TaskProgressBroker` 协议或基础类，给 WebSocket 订阅使用。
- `RedisTaskProgressBroker` 使用 Redis Pub/Sub 订阅 `TASK_PROGRESS_CHANNEL` 并按 `task_id` 过滤事件。
- `InMemoryTaskProgressBroker` 只用于测试，提供 `publish(event)` 和 `subscribe(task_id)`。

## 任务 4：实现 Task Center API

- [ ] **步骤 1：改造 `apps/api-service/src/visiox_api/main.py`**

注册：

- `tasks_router`
- `task_progress_router`

保留现有 `GET /health` 行为不变。

- [ ] **步骤 2：创建 `apps/api-service/src/visiox_api/routes/tasks.py`**

实现：

- Pydantic request/response schema。
- Dependency：
  - `get_task_session()` 默认使用 `visiox_db.session.get_session`。
  - `get_stream_producer()` 默认创建 Redis stream producer。
- `POST /tasks`：
  - 输入：`task_type`、`resource_refs`、`payload`
  - 创建 `Task(status=QUEUED)`。
  - commit 后 enqueue `TaskCommand`。
  - 返回任务详情。
  - 如果 enqueue 失败，任务应标记为 `FAILED`，记录 `error_code="ENQUEUE_FAILED"` 和错误信息。
- `GET /tasks`：
  - 支持简单分页 `limit`、`offset`。
- `GET /tasks/{task_id}`。
- `POST /tasks/{task_id}/cancel`：
  - 仅允许取消 `PENDING` 或 `QUEUED`。
  - 其他状态返回 `409`。
- `update_task_progress(session, task_id, event)` helper：
  - 更新任务状态、progress、stage、error 字段。
  - `SUCCESS`、`FAILED`、`CANCELED` 设置 `finished_at`。

## 任务 5：实现任务进度 WebSocket

- [ ] **步骤 1：创建 `apps/api-service/src/visiox_api/ws/tasks.py`**

实现：

- `router = APIRouter()`。
- Dependency：`get_progress_broker()`。
- `@router.websocket("/ws/tasks/{task_id}")`：
  - accept connection。
  - 订阅 broker 的 `task_id` 事件。
  - 每个事件用 `websocket.send_json(event.model_dump(mode="json"))` 推给客户端。
  - 客户端断开时正常退出。

## 任务 6：验证和提交

- [ ] **步骤 1：安装依赖**

运行：

```bash
python -m pip install -e ".[test,dev]"
```

- [ ] **步骤 2：运行 Task Center 测试**

运行：

```bash
pytest tests/integration/test_task_center.py -v
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
ruff check apps packages tests infra
```

预期：通过。

- [ ] **步骤 5：提交**

运行：

```bash
git add pyproject.toml apps/api-service/src/visiox_api packages/visiox-common/src/visiox_common/tasks.py packages/visiox-messaging tests/integration/test_task_center.py docs/superpowers/plans/2026-07-03-task-center-messaging.md
git commit -m "feat: add task center messaging"
```

## 自查

- 规格覆盖：本计划覆盖 Task 3 要求的任务状态模型、Redis Stream producer/consumer、Pub/Sub progress publisher、任务 API、任务进度 WebSocket、任务创建/入队/状态更新/进度事件测试。
- 有意延后：不实现真实 worker、不实现业务模块触发任务、不实现前端 Task Center 页面、不要求本地真实 Redis/Docker。
- 数据一致性：数据库 `tasks` 表是最终状态事实来源；Redis 只负责命令投递和实时通知，不作为最终状态事实来源。
- 文档语言：本文档正文使用中文；代码、路径、命令、API、状态值和消息字段保持英文。
