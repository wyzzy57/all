# Edge Agent 与部署 Worker 执行计划

**任务编号：** Task 10

**目标：** 实现平台到边缘设备的控制链路：注册设备、管理摄像头、调用 Edge Agent 部署/停止/回滚边缘应用，并把部署状态写入任务中心和部署记录。

**架构：** API 服务负责设备、摄像头和部署任务的 CRUD 与入队；`deployment-worker` 负责读取部署任务并调用设备侧 Edge Agent；`edge-agent` 是设备侧控制面 API，只管理边缘应用包生命周期，不包含 YOLO 推理逻辑。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2.x、Redis Task Center、对象存储抽象、httpx、pytest。

## 范围

- 新增 Edge Agent：
  - `GET /health`
  - `GET /device/info`
  - `POST /apps/deploy`
  - `POST /apps/{app_id}/start`
  - `POST /apps/{app_id}/stop`
  - `POST /apps/{app_id}/rollback`
  - `GET /apps`
  - `POST /camera/test`
- 新增平台设备 API：
  - 注册、查询和列出设备。
  - 创建、查询和列出摄像头。
  - 调用 Agent 做设备健康检查和摄像头连接测试。
- 新增平台部署 API：
  - 创建部署任务。
  - 查询部署记录。
  - 停止部署。
  - 回滚部署。
- 新增 deployment-worker：
  - 原子领取部署任务。
  - 下载 edge app package。
  - 调用 Agent deploy/start/stop/rollback。
  - 写入 deployment/task/device 状态和日志 URI。
  - 失败时标记可重试。

## 非目标

- 不实现真实 Docker 容器运行。
- 不实现 YOLO 推理服务。
- 不实现真实 RTSP 抓帧。
- 不做认证和权限系统。
- 不做前端页面。

## 数据约定

### Device

- `status`：`offline`、`online`、`deploying`、`error`。
- `resource_info`：Agent 返回的设备资源摘要。

### Camera

- `status`：`inactive`、`active`、`error`。
- `last_snapshot_uri`：本任务只保留字段，不生成真实截图。

### Deployment

- `status`：`pending`、`deploying`、`running`、`stopped`、`failed`、`rolled_back`。
- `active`：当前设备上是否为激活版本。
- `logs_uri`：worker 写入部署日志对象。

### Task payload

部署任务：

```json
{
  "action": "deploy",
  "deployment_id": "...",
  "device_id": "...",
  "edge_app_version_id": "..."
}
```

停止任务：

```json
{
  "action": "stop",
  "deployment_id": "...",
  "device_id": "..."
}
```

回滚任务：

```json
{
  "action": "rollback",
  "deployment_id": "...",
  "device_id": "...",
  "target_edge_app_version_id": "..."
}
```

## 文件变更

### 1. `apps/edge-agent/src/visiox_edge_agent/apps.py`

- 定义内存态 app registry。
- 提供 deploy/start/stop/rollback/list 操作。
- 只记录 package URI、manifest、status，不启动真实容器。

### 2. `apps/edge-agent/src/visiox_edge_agent/docker_runtime.py`

- 定义 `DockerRuntime` 占位控制器。
- 第一版以可测试的内存模拟实现 start/stop。

### 3. `apps/edge-agent/src/visiox_edge_agent/main.py`

- 创建 FastAPI app。
- 暴露 health、device info、apps、camera test API。

### 4. `apps/api-service/src/visiox_api/routes/devices.py`

- 注册/查询/列出设备。
- 创建/查询/列出摄像头。
- 通过 httpx 调用 Agent health 和 camera test。

### 5. `apps/api-service/src/visiox_api/routes/deployments.py`

- 创建 deployment 并提交 `DEPLOY_APP` 任务。
- 停止 deployment 并提交 `STOP_APP` 任务。
- 回滚 deployment 并提交 `ROLLBACK_APP` 任务。
- 查询/list deployment。
- enqueue 失败时标记 deployment/task failed。

### 6. `workers/deployment-worker/src/visiox_deployment_worker/main.py`

- 定义 Agent client。
- 定义 `run_deployment_task`。
- 支持 deploy、stop、rollback。
- 原子领取 task，校验 payload，写日志到对象存储。

### 7. `tests/integration/test_deployment_flow.py`

- 覆盖 Agent API。
- 覆盖设备和摄像头 API。
- 覆盖部署/停止/回滚 API 入队。
- 覆盖 worker 成功部署、失败标记、payload mismatch、重复领取保护。

## 实施步骤

1. 创建本计划并提交。
2. 实现 Edge Agent 内存控制面。
3. 实现 devices API 和 deployments API。
4. 实现 deployment-worker。
5. 增加 integration tests。
6. 将新包加入 `pyproject.toml` 构建和 pytest `pythonpath`。
7. 运行聚焦测试、全量测试和 ruff。
8. 修复评审问题。
9. 提交为 `feat: add edge deployment flow`。

## 验收标准

- 平台可以注册设备并测试 Agent health。
- 平台可以创建摄像头并通过 Agent 做连接测试。
- 平台可以对 ready 的 edge app version 创建部署任务。
- worker 可以调用测试 Agent 完成 deploy/start/stop/rollback。
- 部署失败在 deployment 和 task 上可见。
- Agent 不包含 YOLO 推理逻辑。
- `pytest` 和 `ruff check` 通过。
