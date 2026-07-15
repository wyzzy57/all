# Visiox NVIDIA 边缘高性能部署设计

日期：2026-07-15
状态：已确认，等待实施计划

## 1. 目标

在 Visiox 中实现生产级 NVIDIA 边缘部署闭环。Visiox 平台没有 GPU，不执行训练、模型导出、TensorRT 编译或推理；所有 GPU 计算都在同一高速局域网中的 Jetson 或 x86 NVIDIA 边缘设备上完成。

首期交付范围：

- Ultralytics YOLO26 目标检测模型。
- 训练成功后的 `best.pt` 模型。
- ONNX 导出、TensorRT FP32/FP16/INT8 候选构建与自动选优。
- NVIDIA Jetson 与 x86 GPU，分别建立兼容资源池。
- TensorRT Native 低延迟运行时。
- NVIDIA Triton 高吞吐运行时。
- 图片 HTTP API 推理。
- Node Agent 自动接入、部署、停止、升级、监控和回滚。
- 运行时镜像与模型发布包分离。
- 平台保存数据、制品、状态和指标，但不需要 NVIDIA GPU。

## 2. 非目标

首期明确不实现：

- RTSP、视频文件、摄像头管理和视频流推理。
- 分割、姿态、OBB、分类和语义分割的 TensorRT 推理适配。
- Jetson 与 x86 GPU 混合执行同一次训练或构建任务。
- 联邦训练、跨广域网训练和异步梯度训练。
- 完全离线、U 盘或人工复制部署包流程。
- Kubernetes/K3s 编排。
- 平台侧 GPU 构建或推理。
- 本设计内实现多机 DDP/NCCL 训练。分布式训练复用本设计的节点基础，但单独设计和实施。

## 3. 已确认的关键决策

| 主题 | 决策 |
| --- | --- |
| 平台定位 | CPU 控制与存储平面，不提供 GPU 计算 |
| GPU 计算位置 | 数据处理、训练、评估、导出、TensorRT 编译和推理均在边缘设备 |
| 目标硬件 | NVIDIA Jetson 与 x86 NVIDIA GPU |
| 资源隔离 | Jetson 与 x86 分池；同一任务仅使用同类兼容资源池 |
| 边缘网络 | 同一高速局域网，节点可直接互访 |
| 设备管理 | 每台设备预装一个 Visiox Node Agent |
| Agent 通信 | Agent 主动通过 HTTPS/WSS 连接平台，设备不开放管理入站端口 |
| 数据集 | 平台 MinIO/NAS 为不可变版本源，训练前同步并缓存到节点本地 SSD |
| 运行模式 | TensorRT Native 低延迟模式与 Triton 高吞吐模式 |
| 优化精度 | 自动优化并允许用户手动锁定 FP32、FP16 或 INT8 |
| 首期任务 | 目标检测 |
| 首期输入 | 图片 HTTP API |
| Agent 实现 | Go 单二进制，amd64/arm64，由 systemd 托管 |
| Native Runtime | C++ TensorRT |

## 4. 总体架构

```text
Visiox 控制与存储平面（无 GPU）
├── Web / API Service
├── Deployment Controller / Task Center
├── Device Gateway
├── PostgreSQL / Redis
├── MinIO 或 NAS
├── OCI Registry
└── 指标、日志、审计与告警
            │
            ├── HTTPS/WSS：命令、ACK、心跳、事件、指标
            └── HTTPS：镜像、数据集、模型和部署制品
                         │
NVIDIA 边缘计算平面
├── Jetson 兼容资源池
├── x86 NVIDIA 兼容资源池
└── 每台设备
    ├── Visiox Node Agent
    ├── 本地 SSD 制品缓存
    ├── Docker + NVIDIA Container Runtime
    ├── 数据准备容器
    ├── 训练/评估容器
    ├── 模型优化容器
    ├── Inference Gateway
    └── TensorRT Native 或 Triton 推理容器
```

平台负责期望状态、任务调度、制品索引、版本、权限、审计和可视化。边缘设备负责所有计算和容器生命周期。平台暂时断开时，已运行的推理服务继续工作，Agent 在本地缓存事件并在恢复连接后补报。

## 5. 控制通道与设备注册

### 5.1 首次注册

1. 管理员在 Visiox 创建一次性注册码。
2. Node Agent 使用注册码调用 `POST /agent/v1/enroll`。
3. 平台返回设备 ID、客户端证书、平台 CA 和初始策略。
4. Agent 保存凭据并建立 WSS 长连接。
5. 后续证书轮换使用设备证书完成，不重复使用注册码。

### 5.2 长连接

Node Agent 主动连接 Device Gateway。边缘设备不向平台开放 SSH、Docker API 或 Agent 管理端口。Redis 仅供平台内部服务使用，不直接暴露给设备。

长连接承载：

- 平台到设备：命令、取消、期望状态和凭证刷新通知。
- 设备到平台：ACK、阶段进度、结果、心跳、资源摘要和告警。

大文件不经过 WSS。Agent 使用短期签名 URL 或短期 Registry Token 从 MinIO/NAS 和 Registry 直接拉取内容。

### 5.3 命令信封

```json
{
  "command_id": "cmd-uuid",
  "node_id": "node-uuid",
  "resource_type": "deployment_instance",
  "resource_id": "instance-uuid",
  "revision": 12,
  "action": "reconcile",
  "desired_state": "running",
  "payload": {},
  "issued_at": "2026-07-15T10:00:00Z",
  "expires_at": "2026-07-15T10:10:00Z"
}
```

`command_id + node_id` 是幂等键。Agent 将已接收命令和最终结果写入本地数据库；同一命令被重发时返回已有进度或结果，不重复创建容器。

## 6. Node Agent

Node Agent 是设备上唯一拥有容器管理权限的 Visiox 组件。推理、构建和训练容器不接触 Docker Socket。

### 6.1 技术形态

- Go 单二进制。
- 构建 `linux/amd64` 与 `linux/arm64` 包。
- 由 systemd 开机自启。
- 状态目录：`/var/lib/visiox-agent`。
- 配置目录：`/etc/visiox-agent`。
- 日志默认写 journald，并保留限额本地任务日志。

选择 Go 的原因是便于生成无 Python/Torch 依赖的单文件 Agent，降低 Jetson 设备安装和升级成本。平台业务服务继续使用 Python/FastAPI，不要求重写。

### 6.2 Agent 内部模块

- Identity Client：注册、证书、轮换和重连。
- Resource Probe：采集 CPU 架构、GPU、显存、Compute Capability、驱动、CUDA、cuDNN、TensorRT、JetPack/L4T、Docker、磁盘和网络标签。
- Artifact Manager：断点续传、校验、签名验证、缓存、引用计数和空间回收。
- Task Executor：幂等执行、阶段检查点、超时、取消和断电恢复。
- Runtime Manager：通过 Docker API 创建容器、网络、卷、GPU 设备请求和资源限制。
- Health Monitor：启动探活、模型预热、持续健康和资源采样。
- Event Spool：本地事件序号、批量补报和服务端确认游标。

### 6.3 本地持久化

Agent 使用本地 SQLite 或等价嵌入式数据库记录：

- 已执行命令和幂等结果。
- 当前活动部署与上一健康修订。
- 本地制品缓存和引用计数。
- 尚未被平台确认的事件。
- 容器 ID、端口、卷和运行时状态。

设备重启后，Agent 先恢复本地最后健康版本，再与平台期望状态对账。

## 7. 资源池与兼容指纹

### 7.1 资源池

至少建立两类资源池：

- Jetson：按 JetPack、L4T、CUDA、TensorRT、GPU 类型和显存分组。
- x86 NVIDIA：按 Linux、驱动、CUDA、TensorRT、GPU Compute Capability 和显存分组。

同一次优化构建或后续分布式训练任务只在一个兼容资源池中执行。调度前必须验证可用显存、磁盘空间、运行时版本和节点在线状态。

### 7.2 TensorRT Engine 指纹

每个 Engine 记录：

- OS 与 CPU 架构。
- GPU 型号、Compute Capability、显存和集成/独立 GPU 类型。
- 驱动、CUDA、cuDNN 和 TensorRT 版本。
- JetPack/L4T 版本。
- precision、batch、动态 shape profile、workspace 和构建参数。
- Native 或 Triton 运行模式。
- 构建镜像 digest。

默认使用精确指纹复用策略。指纹不兼容时在目标资源池重新构建。TensorRT 默认会检查构建与运行版本及 GPU Compute Capability；JetPack 当前不支持 TensorRT 的硬件兼容模式，因此不得将 x86 构建结果直接假定为 Jetson 可用。参考 [NVIDIA TensorRT Engine Compatibility](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/engine-compatibility.html)。

## 8. 模型发布与自动优化

### 8.1 发布流水线

1. 选择 ready 的 `TrainedModel` 和对应 `best.pt`。
2. 锁定训练任务、数据集版本、类别、输入尺寸和预处理配置。
3. 在目标资源池的构建节点导出 ONNX。
4. 使用金标样本比较 PyTorch 与 ONNX 输出。
5. 构建 FP32 基准和 FP16 候选。
6. 有校准数据时构建 INT8 候选。
7. 在同一硬件指纹上执行精度、性能和稳定性测试。
8. 自动选择满足门禁且符合运行模式目标的候选。
9. 用户可以手动锁定 FP32、FP16 或 INT8。
10. 发布不可变 ModelRelease 修订。

Ultralytics 当前支持 YOLO26 TensorRT 导出及 INT8 校准，并建议在实际部署设备上执行 INT8 导出。参考 [Ultralytics TensorRT Export](https://docs.ultralytics.com/integrations/tensorrt)。

### 8.2 自动选择规则

- FP32：正确性与精度基准，通常不自动成为最终版本。
- FP16：无校准集时的默认候选。
- INT8：只有在精度门禁通过且相较 FP16 有实际收益时才自动选中。
- 默认精度门禁：`mAP50-95` 绝对下降不超过 1 个百分点。
- 低延迟模式：在通过门禁的候选中优先选择 p95 总时延最低者。
- 高吞吐模式：在通过门禁的候选中优先选择目标并发下 QPS/FPS 最高者。
- 用户覆盖选择仍必须通过可加载、输出结构和基础精度检查。

### 8.3 基准方法

- 固定验证数据集版本和样本顺序。
- 先执行预热，再计入测量。
- Native 至少测试 concurrency 1。
- Triton 至少测试 concurrency 1、4、8、16 和候选 batch profile。
- 记录 p50、p95、p99、QPS/FPS、GPU 利用率、峰值显存、CPU、内存和构建耗时。
- 记录 mAP50、mAP50-95、precision 和 recall。

## 9. 模型发布包

运行时镜像和模型发布包分离。每次训练后不重新构建整张推理镜像；Agent 分别拉取按 digest 固定的运行时镜像和不可变模型包。

```text
release-v12/
├── manifest.json
├── model/
│   ├── source-best.pt
│   ├── model.onnx
│   ├── model-fp16.engine
│   └── model-int8.engine
├── config/
│   ├── labels.json
│   ├── preprocess.json
│   ├── postprocess.json
│   ├── native-runtime.json
│   └── triton/config.pbtxt
├── evidence/
│   ├── validation.json
│   ├── benchmark.json
│   └── calibration.cache
└── security/
    ├── checksums.sha256
    └── signature.json
```

`manifest.json` 必须记录来源训练模型、数据集版本、输入输出契约、兼容指纹、运行时镜像 digest、制品 SHA256 和签名信息。

## 10. 双运行时与统一推理协议

### 10.1 设备入口

设备运行独立的 Edge Router，负责稳定外部端口和服务实例名到活动修订的路由。Node Agent 通过 Router 的本地管理接口写入路由并执行原子切换，但 Agent 本身不承载推理流量。Edge Router 不处理模型语义，只负责 TLS、连接管理和 A/B 修订路由。

每个部署修订包含一个 Inference Gateway：

- 统一鉴权、限流和请求大小限制。
- 校验 JPEG/PNG。
- 统一解码、letterbox、归一化、NMS、坐标恢复和响应编码。
- 记录阶段耗时。
- 通过 Native Adapter 或 Triton Adapter 调用后端。

### 10.2 TensorRT Native

- C++ TensorRT。
- Gateway 进程内加载 Engine 或与 Native Runtime 链接。
- 首期以 batch 1 和低队列等待为优化目标。
- 非特权容器、只读根文件系统、固定资源限制。

### 10.3 Triton

- 使用按 digest 固定的 Triton 运行时镜像。
- Gateway 通过 localhost gRPC 调用 Triton。
- 使用 Model Repository 和 `config.pbtxt` 管理模型配置。
- 支持动态批处理和并发模型实例。参考 [NVIDIA Triton Dynamic Batching](https://docs.nvidia.com/deeplearning/triton-inference-server/archives/triton-inference-server-2670/user-guide/docs/user_guide/batcher.html)。

### 10.4 图片 API

```http
POST /v1/services/{instance_name}/infer
Content-Type: multipart/form-data
X-Request-ID: optional
Authorization: Bearer <service-token>
```

允许配置请求大小、超时、并发和置信度阈值范围。响应示例：

```json
{
  "request_id": "req-uuid",
  "service": "pepper-detect",
  "model_release": "v12",
  "runtime": "native-tensorrt",
  "image": {"width": 1920, "height": 1080},
  "detections": [
    {
      "class_id": 0,
      "label": "pepper",
      "score": 0.9231,
      "bbox": {"x1": 120.4, "y1": 85.2, "x2": 411.8, "y2": 390.1}
    }
  ],
  "timing_ms": {
    "queue": 0.2,
    "preprocess": 1.7,
    "inference": 4.8,
    "postprocess": 0.9,
    "total": 8.1
  }
}
```

运维端点：

- `GET /health/live`
- `GET /health/ready`
- `GET /v1/metadata`
- `GET /metrics`
- `POST /v1/warmup`，仅 Agent 可调用

Native 与 Triton 必须使用同一字段、类别语义、坐标系和错误格式。

## 11. 部署状态机与回滚

### 11.1 内部状态

```text
queued
  -> accepted
  -> downloading
  -> preparing
  -> starting
  -> warming
  -> healthy

任一阶段失败
  -> failed
  -> rolling_back
  -> previous_healthy
```

前端将内部状态归并为部署中、运行中、运行中止、部署失败。`POST /services` 创建服务后必须首先返回 `deploying`；只有边缘实例通过 Engine 加载、标准图片预热和 readiness 检查后才能显示 `running`。

### 11.2 蓝绿升级

1. 当前修订 A 保持服务。
2. 新修订 B 使用临时端口启动。
3. Agent 对 B 执行 Engine 加载、标准图片预热和健康检查。
4. Node Agent 原子更新 Edge Router，将稳定入口从 A 切换到 B。
5. A 在可配置回滚窗口内保留。
6. B 异常时恢复 A。

部署失败不能覆盖上一健康修订。停止服务时先停止接收新请求，再等待在途请求或超时后关闭容器。

## 12. 数据模型

### 12.1 复用对象

- `TrainingPipeline`
- `TrainingJob`
- `TrainedModel`
- `Task`
- `DeploymentService` 产品概念

### 12.2 新增对象

#### ResourcePool

- `name`
- `kind`: `jetson` 或 `x86_nvidia`
- `selector`
- `compatibility_policy`
- `enabled`

#### ComputeNode

- `name`
- `resource_pool_id`
- `status`: `enrolling | online | offline | draining | disabled`
- `architecture`
- `capabilities`
- `resources`
- `fingerprint`
- `agent_version`
- `certificate_serial`
- `last_seen_at`

#### ModelRelease

- `trained_model_id`
- `version`
- `task`
- `dataset_version`
- `source_artifact_uri`
- `onnx_artifact_uri`
- `input_spec`
- `class_schema`
- `manifest`
- `checksum`
- `status`: `draft | optimizing | ready | failed`

#### OptimizationBuild

- `model_release_id`
- `node_id`
- `resource_pool_id`
- `fingerprint`
- `runtime_mode`
- `precision`
- `profile`
- `artifact_uri`
- `validation_metrics`
- `benchmark_metrics`
- `status`: `queued | building | validating | ready | rejected | failed`
- `error`

#### DeploymentRevision

- `service_id`
- `revision`
- `model_release_id`
- `optimization_build_id`
- `runtime_mode`
- `runtime_image_digest`
- `config`
- `checksum`
- `created_at`

#### DeploymentInstance

- `service_id`
- `deployment_revision_id`
- `node_id`
- `instance_name`
- `status`
- `endpoint`
- `container_ids`
- `active`
- `healthy_at`
- `stopped_at`
- `error`

#### NodeCommand / NodeEvent

- 幂等命令、revision、期望状态、ACK、事件序号、阶段、结果和时间戳。

`DeploymentService` 不再独立保存一个未经验证的 `running` 状态；它的展示状态由活动 `DeploymentInstance` 聚合得出。

## 13. API 边界

### 13.1 Agent 与节点

- `POST /agent/v1/enroll`
- `WS /agent/v1/connect`
- `GET /nodes`
- `GET /nodes/{node_id}`
- `GET /resource-pools`
- `POST /nodes/{node_id}/drain`

### 13.2 模型发布与优化

- `POST /trained-models/{trained_model_id}/releases`
- `GET /model-releases`
- `GET /model-releases/{release_id}`
- `POST /model-releases/{release_id}/optimize`
- `GET /optimization-builds/{build_id}`
- `GET /model-releases/{release_id}/candidates`
- `POST /model-releases/{release_id}/select-candidate`

### 13.3 服务部署

- `POST /services`
- `GET /services`
- `GET /services/{service_id}`
- `POST /services/{service_id}/stop`
- `POST /services/{service_id}/redeploy`
- `POST /services/{service_id}/rollback`
- `GET /services/{service_id}/events`
- `DELETE /services/{service_id}`，先安全停止再删除业务记录

所有构建和部署操作都是异步任务。API 返回资源与 task ID，前端通过事件流或轮询显示真实阶段。

## 14. 可观测性

平台展示：

- 请求数、成功率、4xx、5xx、超时和限流。
- queue、preprocess、inference、postprocess、total 的 p50、p95、p99。
- QPS/FPS、batch、队列深度和并发实例。
- GPU 利用率、显存、温度、功耗、CPU、内存、磁盘和重启次数。
- 活动模型版本、精度、运行时、Engine 加载时间和回滚次数。

设备本地可暴露 Prometheus 格式指标；Agent 定期聚合并通过出站通道推送平台。默认不上传用户推理图片或检测结果。样本留存必须由用户显式开启，并设置保留期和访问权限。

## 15. 安全与供应链

- Agent 与平台使用设备证书和 TLS。
- 注册码一次性且短时有效。
- 制品下载使用短期凭证。
- 运行时镜像使用 digest，不使用漂移的 `latest` 标签。
- 模型包记录 SHA256 和签名。
- Agent 仅接受平台签名且符合目标节点的清单。
- TensorRT Engine 只从可信构建任务生成。NVIDIA 明确提醒 Engine 包含可执行代码，不应反序列化不可信 Engine。
- 推理和构建容器默认非特权、只读根文件系统、最小 Linux capabilities。
- Docker Socket 仅供 Agent 访问。
- 所有部署、停止、回滚、候选覆盖和密钥操作进入审计日志。
- 商业发布前生成镜像与依赖 SBOM，并单独完成 Ultralytics、TensorRT、Triton 和基础镜像许可证审查。

## 16. 当前代码迁移边界

现有代码的主要问题：

- `apps/yolo26-inference` 当前 `load_predictor()` 返回 `DeterministicPredictor`，不是真实 TensorRT 推理。
- `POST /services` 当前在数据库写入后立即将服务标记为 `running`。
- 当前服务预测请求仍由平台 API 加载模型执行，计算未发生在边缘服务实例。
- 当前 `environment` 和 `instance_name` 只是记录字段，没有节点调度和真实容器实例。

迁移原则：

- 保留现有前端“部署”和“服务列表”工作流。
- 将创建服务改为创建 DeploymentRevision、DeploymentInstance 和异步部署任务。
- 服务在线体验改为请求真实边缘 endpoint，不再调用平台内的训练模型预测路径。
- 现有 deterministic predictor 仅保留为单元测试替身，不进入生产运行时。
- 现有历史服务记录迁移为 `legacy` 或 `stopped`，不得虚构健康实例。
- 不恢复旧的写死设备、摄像头或边缘应用列表；新增节点数据全部来自 Agent 注册。

## 17. 实施阶段

### M1：节点接入

- Go Node Agent 骨架、systemd 包、amd64/arm64 构建。
- 注册、证书、WSS、心跳、重连和本地事件日志。
- 资源探测、ComputeNode、ResourcePool 和管理 API。
- 在一台 Jetson 与一台 x86 GPU 设备完成接入验收。

### M2：边缘模型优化

- ModelRelease 与 OptimizationBuild。
- 构建容器和 Agent 构建任务执行器。
- PT 到 ONNX、FP32/FP16/INT8 Engine。
- 一致性、精度、Benchmark、自动选优和制品发布。

### M3：Native 真实部署

- C++ TensorRT Native Runtime。
- Inference Gateway 和统一图片 API。
- DeploymentRevision、DeploymentInstance 和真实 `/services` 状态。
- Agent 容器启动、探活、预热、停止和日志回传。

### M4：Triton 与双模式

- Triton Model Repository 生成。
- Gateway Triton gRPC Adapter。
- 动态批处理和并发配置。
- Native/Triton API 契约和金标结果一致性测试。

### M5：生产强化

- 蓝绿切换、回滚、断电恢复、平台断连补报。
- 签名、镜像 digest、短期凭证、限流和审计。
- 故障注入、持续压测、资源泄漏和升级验证。
- 平台指标、部署事件和告警界面。

## 18. 测试与验收

### 18.1 自动化测试

- 平台 API、状态机、幂等命令和数据库迁移测试。
- Agent 命令重发、断点下载、校验失败、断电恢复和事件补报测试。
- Native/Triton API 契约测试。
- PT、ONNX、FP16、INT8 金标图片一致性测试。
- Engine 指纹兼容和拒绝测试。
- 模型包路径安全、SHA256、签名和损坏制品测试。
- 蓝绿切换、预热失败、显存不足、容器崩溃和回滚测试。

### 18.2 硬件验收

- 平台机器没有 NVIDIA GPU 时可以完成创建、调度、监控和回滚。
- 至少一台 Jetson 与一台 x86 GPU 完成 Agent 注册和真实部署。
- 服务创建后先显示 `deploying`，只有设备预热和探活通过才显示 `running`。
- FP16/INT8 候选具有精度、p95、吞吐、显存和兼容指纹证据。
- Native 与 Triton 对同一组金标图片返回相同 API 契约和可接受的数值误差。
- 平台断连、设备重启、损坏 Engine、显存不足和新版本失败不破坏上一健康服务。
- 当前服务在升级与自动回滚过程中保持稳定 HTTP 地址。
- 持续压测期间无持续内存/显存增长，错误率和延迟符合发布门禁。

## 19. 分布式训练的后续边界

平台后续支持边缘分布式训练，但不纳入本设计实施：

- 复用 ComputeNode、ResourcePool、Node Agent、Task、证书、心跳、数据集缓存和指标通道。
- Jetson 与 x86 分池，同一次 DDP 训练只使用一个兼容资源池。
- 数据集版本在训练前同步到每个节点本地 SSD。
- 训练节点通过高速局域网和 NCCL 直接通信，平台不转发数据或梯度。
- 后续单独定义 TrainingJobRun、rendezvous、rank、弹性恢复、Checkpoint 和多节点故障语义。

高性能部署实施不得将 Agent 协议写死为只能执行部署命令；任务信封和能力声明必须允许后续新增 `dataset_sync`、`train`、`evaluate` 和 `optimize` 执行器。

## 20. 完成定义

当 M1-M5 的自动化测试与 Jetson/x86 硬件验收全部通过，并且当前 Visiox 服务部署入口已经调用真实边缘实例而非平台内预测函数时，本高性能部署子项目才视为完成。
