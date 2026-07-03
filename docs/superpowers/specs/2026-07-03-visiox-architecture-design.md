# Visiox 综合模型管理平台架构设计 v0.1

日期：2026-07-03

## 1. 目标

Visiox 是一个私有化部署在客户内网的综合模型管理平台。第一版聚焦 Ultralytics YOLO26 六类任务的端到端闭环：

```text
模型空间 -> 创建训练产线 -> 数据准备 -> Label Studio 标注同步 -> 数据格式检验
-> YOLO26 训练 -> 训练模型版本 -> 导出 -> 边缘应用打包 -> 边缘设备部署 -> 摄像头推理
```

平台不做用户和权限体系，定位为内部使用项目。但保留任务日志、操作记录和系统配置，保证问题可追溯。

架构图参考：[architecture-v01.html](../../architecture-v01.html)。

## 2. 关键决策

| 主题 | 决策 |
| --- | --- |
| 部署形态 | 私有化客户内网部署 |
| 编排方式 | 第一版使用 Docker Compose |
| 后端技术栈 | Python + FastAPI |
| 前端技术栈 | Vue 3 + TypeScript + Vite + Element Plus + Pinia |
| 数据库 | PostgreSQL |
| 服务通信 | Redis Stream 做可靠任务队列，Redis Pub/Sub 做实时通知 |
| 文件/制品存储 | 默认 MinIO，支持对接客户已有对象存储/NAS |
| 镜像仓库 | 默认 Registry，可扩展 Harbor |
| 标注系统 | 集成 Label Studio |
| 权限体系 | 不做登录、角色、权限；保留操作/任务记录 |
| 边缘连接 | 平台主动访问边缘设备 Edge Agent |
| 边缘运行方式 | 支持 Docker 和本机进程，第一版主路径 Docker |
| 推理服务 | 统一 YOLO26 推理镜像，通过配置加载不同任务模型 |

## 3. Docker Compose 服务

第一版服务按容器拆分：

| 服务 | 职责 |
| --- | --- |
| `frontend` | Web 管理台 |
| `api-service` | FastAPI 管理 API、业务入口、OpenAPI |
| `training-worker` | 训练、验证、导出 YOLO26 模型 |
| `deployment-worker` | 边缘应用部署、停止、回滚 |
| `label-sync-worker` | Label Studio 数据和标注结果同步 |
| `camera-worker` | 摄像头连通性测试、截图、样本采集 |
| `postgres` | 核心业务数据 |
| `redis` | Stream、Pub/Sub、短期状态缓存 |
| `minio` | 模型权重、数据集、训练产物、部署包 |
| `registry` | 边缘应用镜像 |
| `label-studio` | 标注服务 |

边缘设备单独安装 `edge-agent`，不作为平台 Compose 的必选容器。

## 4. 核心模块

管理台第一版包含：

- 模型空间
- 训练产线
- 训练任务
- 数据准备
- 数据集
- Label Studio 标注同步
- 摄像头管理
- 边缘设备管理
- 边缘应用
- 部署任务
- 任务中心
- 系统配置
- 操作/任务日志

## 5. 数据准备模块

数据准备模块是独立的一等模块，不只存在于创建产线向导中。它负责把原始数据上传到平台、组织成数据集、发起 Label Studio 标注、同步标注结果，并为训练产线提供可用数据。

第一版支持两条入口：

1. 平台内上传数据
   - 上传图片或压缩包
   - 选择任务类型
   - 创建数据集
   - 管理样本、类别、标签状态
   - 执行数据分析、可视化和格式检验

2. Label Studio 标注闭环
   - 从平台数据集创建 Label Studio 项目
   - 将样本同步到 Label Studio
   - 用户在 Label Studio 中标注
   - 平台同步标注结果
   - 转换为内部 annotation schema
   - 生成 Ultralytics 训练格式

数据准备模块的核心对象：

```text
dataset
- id
- name
- task
- status
- class_schema
- sample_count
- annotation_count
- source: upload | label_studio | camera_capture
- storage_uri

dataset_sample
- id
- dataset_id
- file_uri
- width
- height
- checksum
- split: train | val | test | unassigned
- annotation_status: unlabeled | labeling | labeled | invalid

annotation
- id
- dataset_sample_id
- source: label_studio | import | manual
- raw_payload_uri
- internal_payload
- validation_status

label_project
- id
- dataset_id
- provider: label_studio
- external_project_id
- sync_status
- last_sync_at
```

数据准备模块输出两类结果：

- 可供训练产线选择的数据集版本
- 可供训练 Worker 使用的 Ultralytics 训练目录和 `data.yaml`

创建产线中的“数据准备”步骤复用该模块能力。用户可以在产线向导内选择已有数据集，也可以跳转到数据准备模块上传数据、创建 Label Studio 标注项目并同步结果。

## 6. 模型空间

模型空间分三类对象。

### 6.1 基础模型

基础模型是 YOLO26 官方预训练模型。平台安装时不强制内置权重，首次使用时从客户内网镜像/文件服务器下载，并缓存到平台对象存储。

基础模型源可配置：

```text
model_source
- id
- name
- type: http | s3 | minio | local_mount
- base_url / bucket / mount_path
- enabled
```

基础模型元数据：

```text
base_model
- id
- family: yolo26
- task: detect | segment | semantic | pose | obb | classify
- scale: n | s | m | l | x
- filename
- source_path
- local_uri
- checksum
- size_bytes
- status: remote_available | downloading | ready | failed
```

训练提交前基础模型必须处于 `ready` 状态。

### 6.2 训练产线

训练产线是可复用配置对象，不等同于一次训练任务。产线保存：

- 产线名称
- YOLO26 任务类型
- 模型尺度
- 基础模型引用
- 数据集和数据处理规则
- 参数模板
- 默认训练环境

### 6.3 训练模型

训练模型是训练任务成功后的模型版本。它可用于：

- 查看指标和日志
- 验证
- 导出
- 打包边缘应用
- 部署到边缘设备

## 7. 创建产线流程

创建产线采用四步向导。

### 7.1 基础信息

用户填写：

- 产线名称
- 任务类型：`detect | segment | semantic | pose | obb | classify`
- 模型尺度：`n | s | m | l | x`

平台根据任务类型和尺度自动确定基础权重文件，例如：

- `detect + n -> yolo26n.pt`
- `segment + s -> yolo26s-seg.pt`
- `semantic + m -> yolo26m-sem.pt`

### 7.2 数据准备

数据准备包含：

- 选择已有数据集，或跳转到数据准备模块上传数据并创建数据集
- 创建或关联 Label Studio 标注项目
- 同步 Label Studio 标注
- 将标注结果同步回平台
- 数据清洗
- 数据集划分：`train / val / test`
- 类别映射
- 数据分析与可视化
- 按任务类型执行格式检验
- 生成 Ultralytics 训练目录和 `data.yaml`

数据分析至少包含：

- 样本数量
- 类别分布
- 标注数量
- 图片尺寸分布
- 空标注比例
- 异常样本列表

### 7.3 参数准备

训练参数分层管理：

- 基础参数：`epochs`、`imgsz`、`batch`、`patience`、`device`、`workers`
- 优化参数：`lr0`、`lrf`、`optimizer`、`weight_decay`
- 增强参数：`hsv_h`、`hsv_s`、`hsv_v`、`degrees`、`translate`、`scale`、`fliplr`
- 高级配置：允许编辑 YAML/JSON，但必须做字段白名单校验

### 7.4 提交训练

提交训练前执行预检查：

- 基础模型状态为 `ready`
- 数据集格式校验通过
- 训练参数校验通过
- 训练环境可用

通过后创建 `TRAIN_MODEL` 任务，由 `training-worker` 消费执行。

## 8. YOLO26 支持范围

第一版只支持 Ultralytics YOLO26，并完整打通六类任务：

| 任务 | 模型族 | 标注类型 | 支持模式 |
| --- | --- | --- | --- |
| 检测 | `YOLO26` | 矩形框 | predict / val / train / export |
| 实例分割 | `YOLO26-seg` | 多边形/实例 mask | predict / val / train / export |
| 语义分割 | `YOLO26-sem` | 多边形/画笔，平台生成 mask | predict / val / train / export |
| 姿态 | `YOLO26-pose` | COCO 17 点人体关键点 | predict / val / train / export |
| 定向检测 | `YOLO26-obb` | 旋转矩形或四点框 | predict / val / train / export |
| 分类 | `YOLO26-cls` | 整图分类 | predict / val / train / export |

每类任务支持五个尺度：`n / s / m / l / x`，共 30 个基础模型权重。

## 9. Label Studio 与数据转换

平台需要独立的 `Dataset Converter`（数据集转换器），负责三层转换：

```text
Label Studio JSON
-> 平台内部 annotation schema
-> Ultralytics 训练格式
```

转换器按任务拆分：

- `labelstudio -> internal`
- `internal -> ultralytics-detect`
- `internal -> ultralytics-seg`
- `internal -> ultralytics-sem`
- `internal -> ultralytics-pose`
- `internal -> ultralytics-obb`
- `internal -> ultralytics-cls`

语义分割规则：

- Label Studio 使用多边形/画笔标注
- 平台按原图尺寸生成语义 mask
- 类别区域不应重叠
- 如发生重叠，默认后导出的标注覆盖先导出的标注，并记录警告

OBB 规则：

- 支持旋转矩形和四点多边形
- 平台内部统一存四点格式：`x1 y1 x2 y2 x3 y3 x4 y4`
- 四点顺序统一为顺时针
- 非四点多边形标记为转换错误，不自动猜测

Pose 规则：

- 第一版固定 COCO 17 点人体关键点
- 关键点名称、顺序、可见性规则固定
- 导出时生成 YOLO pose 所需配置，例如 `kpt_shape`、`flip_idx`

## 10. 任务中心

所有长任务统一进入任务中心。

任务类型：

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

任务状态：

```text
PENDING -> QUEUED -> RUNNING -> SUCCESS
                            -> FAILED
                            -> CANCELED
```

任务必须记录：

- 任务类型
- 当前状态
- 进度
- 关联资源
- 当前阶段
- 错误码
- 错误信息
- 是否可重试
- 创建时间和结束时间

## 11. Redis 通信规则

Redis 职责限定如下：

- Redis Stream：可靠任务队列
- Redis Pub/Sub：实时通知
- Redis Key：心跳、进度、短期运行状态

Redis 不作为核心业务数据的事实来源。核心数据写 PostgreSQL，大文件写 MinIO/NAS，镜像写 Registry/Harbor。

推荐命名：

```text
stream:training.commands
stream:training.events
stream:deployment.commands
stream:deployment.events
stream:label_sync.commands
stream:camera.commands

pubsub:task.progress
pubsub:device.status.changed

device:{device_id}:heartbeat
task:{task_id}:progress
```

## 12. 边缘设备与 Edge Agent

平台主动访问边缘设备上的 Edge Agent。Agent 是设备侧控制入口，不负责算法推理。

Agent 职责：

- 接收平台部署命令
- 控制 Docker/本机进程
- 管理部署目录
- 启动、停止、回滚边缘应用
- 返回日志、状态、健康检查
- 上报设备资源
- 辅助摄像头测试和截图

第一版 Agent API：

- `GET /health`
- `GET /device/info`
- `POST /apps/deploy`
- `POST /apps/{id}/start`
- `POST /apps/{id}/stop`
- `POST /apps/{id}/rollback`
- `GET /apps`
- `GET /apps/{id}/logs`
- `POST /camera/test`

## 13. 边缘应用

边缘应用是平台部署到设备的业务单元，不只是模型文件。

一个边缘应用版本包含：

```text
edge-app-version
├── app.yaml
├── model/
│   └── best.pt 或 exported.onnx
├── config/
│   ├── runtime.yaml
│   ├── cameras.yaml
│   └── rules.yaml
└── services/
    └── yolo26-inference
```

第一版采用统一 YOLO26 推理镜像，通过配置加载不同任务模型。

推理服务配置示例：

```yaml
task: detect
model_path: /app/model/best.pt
device: cuda:0
input:
  type: rtsp
  cameras:
    - camera_id: camera-001
      rtsp_url: rtsp://example
output:
  type: http
confidence: 0.25
iou: 0.7
```

推理服务第一版接口：

- `GET /health`
- `GET /model/info`
- `POST /predict/image`
- `POST /predict/video-frame`
- `POST /runtime/reload`
- `GET /metrics`

摄像头实时业务推理由推理服务直接拉 RTSP；Agent 只做摄像头测试和截图辅助。

## 14. MVP 范围

第一版必须打通：

- Docker Compose 部署平台基础服务
- 模型空间三类对象：基础模型、训练产线、训练模型
- 数据准备模块：数据上传、数据集管理、样本管理、数据分析、格式检验
- YOLO26 六类任务和五个尺度
- 基础模型首次使用下载与缓存
- Label Studio 数据同步和标注回流
- 数据集转换器
- 创建产线四步向导
- 训练任务执行、日志、指标和模型版本登记
- 模型导出
- 边缘应用打包
- Edge Agent 部署接口
- 统一 YOLO26 推理镜像
- 部署任务和任务中心

暂不做：

- 用户、角色、权限体系
- SaaS 多租户
- Kubernetes / K3s
- 非 YOLO26 模型族
- 自定义 pose 关键点模板
- 复杂告警中心

## 15. 未决事项

- 内网模型文件服务器协议和路径规范需落地。
- YOLO26-sem 的 Ultralytics 训练目录细节需要用官方示例验证。
- Label Studio 各任务标注模板需要单独设计和测试。
- 训练环境选择需要定义 GPU 发现方式和资源占用策略。
- Edge Agent 鉴权方式虽不做用户体系，但仍需设备级 token 或内网白名单。
