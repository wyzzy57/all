# 模型导出与边缘应用打包执行计划

**任务编号：** Task 9

**目标：** 在已有训练模型版本基础上，提供模型导出任务和边缘应用版本打包能力。第一版只完成平台侧的导出、打包、登记和任务状态更新，不执行真实设备部署，不构建真实 Docker 镜像。

**架构：** API 服务负责创建边缘应用、提交打包任务和查询版本；`training-worker` 继续承担训练后模型导出和边缘包生成；`visiox-yolo26` 提供 YOLO26 export 命令构建和边缘应用包写入工具；对象存储保存导出模型和 `.tar.gz` 边缘应用包。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2.x、Redis Task Center、对象存储抽象、tarfile、hashlib、pytest。

## 范围

- 新增 YOLO26 模型导出命令构建器。
- 新增边缘应用包生成工具。
- 新增边缘应用 API：
  - 创建/查询 edge app。
  - 基于训练模型创建 edge app version。
  - 提交打包任务到任务中心。
- 新增 worker 打包流程：
  - 原子领取任务。
  - 校验 trained model、edge app version 和 payload。
  - 下载训练模型 artifact。
  - 构建 export 命令并运行可注入 runner。
  - 生成边缘应用包并上传对象存储。
  - 更新 edge app version、edge app 和 task 状态。
- 新增集成测试覆盖 API、打包内容、失败清理和重复任务保护。

## 非目标

- 不调用真实 Ultralytics。
- 不生成真实 TensorRT/OpenVINO engine。
- 不构建或推送 Docker 镜像。
- 不实现 Edge Agent 部署、停止、回滚。
- 不实现统一 YOLO26 推理服务。
- 不改前端。

## 数据约定

### EdgeApp

- `name`：边缘应用名，全局唯一。
- `description`：说明。
- `status`：`draft`、`packaging`、`ready`、`failed`。

### EdgeAppVersion

- `edge_app_id`：所属应用。
- `trained_model_id`：来源训练模型。
- `version`：版本号，默认使用训练模型版本和短 ID。
- `package_uri`：打包完成后写入，创建时使用 `pending`。
- `manifest`：包清单、runtime 配置、导出信息和镜像引用。
- `checksum`：包文件 SHA256。
- `status`：`queued`、`packaging`、`ready`、`failed`。

### Task payload

```json
{
  "edge_app_id": "...",
  "edge_app_version_id": "...",
  "trained_model_id": "...",
  "export_format": "onnx",
  "runtime": {
    "image": "registry.local/visiox/yolo26-inference:0.1.0",
    "device": "cpu",
    "confidence": 0.25,
    "iou": 0.7
  },
  "cameras": [],
  "rules": {}
}
```

## 文件变更

### 1. `packages/visiox-yolo26/src/visiox_yolo26/export/commands.py`

- 定义 `ExportCommand`。
- 定义 `build_export_command(model_path, format, output_dir, imgsz=None, half=False, device=None)`。
- 支持格式：`onnx`、`torchscript`。
- 拒绝未知格式和危险参数。
- 返回 argv 列表，不返回拼接字符串。

### 2. `packages/visiox-yolo26/src/visiox_yolo26/edge_app/package.py`

- 定义 `EdgeAppPackageSpec`。
- 定义 `EdgeAppPackageResult`。
- 定义 `build_edge_app_package(spec, output_path)`。
- 写入：
  - `app.yaml`
  - `model/exported.<format>`
  - `config/runtime.yaml`
  - `config/cameras.yaml`
  - `config/rules.yaml`
  - `services/yolo26-inference/image.txt`
- 输出 `.tar.gz`，计算 SHA256。
- tar entry 使用固定安全路径，不接收用户路径。

### 3. `apps/api-service/src/visiox_api/routes/edge_apps.py`

- `POST /edge-apps`
- `GET /edge-apps`
- `GET /edge-apps/{edge_app_id}`
- `POST /edge-apps/{edge_app_id}/versions`
- `GET /edge-apps/{edge_app_id}/versions`
- 版本创建时校验训练模型必须 `ready` 且有 artifact。
- 创建 task：`task_type=EXPORT_MODEL`，`resource_type=edge_app_version`。
- enqueue 失败时将 version/app/task 标记 failed。

### 4. `apps/api-service/src/visiox_api/main.py`

- 注册 `edge_apps` router。

### 5. `workers/training-worker/src/visiox_training_worker/export_flow.py`

- 定义 `ExportCommandRunner` 协议。
- 定义 `run_edge_app_packaging(session, storage, runner, task_id, edge_app_version_id, work_dir)`。
- 原子领取 queued task。
- 校验 task payload 和数据库资源一致。
- 下载 trained model artifact。
- 调用 export command runner。
- 打包并上传 `edge-apps/{version_id}/package.tar.gz`。
- 成功写入 `EdgeAppVersion.package_uri/checksum/manifest/status`。
- 失败时标记 version/app/task failed，并清理已上传对象。
- 对已成功版本保持幂等返回。

### 6. `tests/integration/test_edge_app_package.py`

- 测试 export command 参数。
- 测试 tar.gz 包结构和 checksum。
- 测试 API 创建 app/version 并入队。
- 测试 enqueue 失败状态回滚。
- 测试 worker 成功导出和打包。
- 测试 worker 拒绝 payload mismatch。
- 测试 worker 已领取任务不重复执行。
- 测试上传失败后的对象清理。

## 实施步骤

1. 创建本计划并提交。
2. 实现 `export.commands` 和 `edge_app.package`。
3. 实现 `edge_apps` API 并注册 router。
4. 实现 `export_flow` worker。
5. 增加集成测试。
6. 运行聚焦测试、全量测试和 ruff。
7. 修复评审问题。
8. 提交为 `feat: add edge app packaging`。

## 验收标准

- 可以从 ready 的训练模型创建 edge app version。
- 创建 version 后产生 `EXPORT_MODEL` 任务并进入队列。
- worker 可以生成可检查的边缘应用 `.tar.gz` 包。
- 包中包含模型、runtime config、camera config、rules 和推理镜像引用。
- 失败路径不会留下半成品数据库状态或已上传包对象。
- 重复消息不会重复导出和打包。
- `pytest` 和 `ruff check` 通过。
