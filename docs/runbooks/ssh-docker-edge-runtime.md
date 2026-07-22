# SSH/Docker 边缘运行时验收手册

本手册用于验收 Visiox 通过 SSH 管理的 NVIDIA x86 GPU 与 NVIDIA Jetson
边缘节点。验收脚本输出统一的 `visiox.edge-acceptance.v1` JSON，可归档到发布
记录或 CI 制品中。

## 安全边界

- 必须从独立可信渠道取得 SSH 主机密钥指纹，并通过
  `-ExpectedHostFingerprint` 显式传入。
- 脚本先调用 `/edge-nodes/scan-host-key`，只有扫描结果与预期指纹逐字节一致时
  才会发送一次性引导密码。
- 密码没有 CLI 参数、环境变量或配置文件入口。执行硬件验收时，脚本通过
  `Read-Host -AsSecureString` 交互读取密码，仅在 bootstrap HTTP 请求构造期间
  短暂转换，随后释放对应 BSTR。
- 不要把密码写入请求模板、PowerShell 历史、终端录屏或验收报告。
- 首次完整验收必须使用尚未由 Visiox 引导的节点。已引导节点应先按运维流程
  清理或使用独立测试节点；重复 bootstrap 会被平台拒绝。

## 验收范围

两份脚本都会验证：

1. 主机指纹确认、bootstrap 和 Ed25519 密钥重新连接；
2. Docker NVIDIA Runtime 与平台兼容性；
3. YOLO26 权重自动导出为 TensorRT FP16；
4. 部署服务 `/health` 与真实 `/predict/image` 图片推理；
5. 一个短 Ultralytics 训练任务及其上传产物；
6. 服务停止与测试服务记录清理。

x86 脚本额外要求 `platform_kind=x86_nvidia`、有效 GPU inventory（来自
`nvidia-smi`）和 NVIDIA Container Runtime。Jetson 脚本额外要求
`platform_kind=jetson`，并验证 `/etc/nv_tegra_release` 对应的
`jetpack_version` 与 `l4t_version`。

## 前置条件

- Visiox API、Redis、PostgreSQL、MinIO、Registry 和 edge-executor-worker 已启动；
- 验收机可以访问平台 API，平台可以通过局域网 SSH 访问边缘节点；
- 边缘节点为 Ubuntu/Linux，已安装 Docker 与 NVIDIA Container Runtime；
- x86 节点已安装兼容 NVIDIA 驱动；Jetson 已安装兼容 JetPack/L4T；
- 已准备一个 detect 训练产线、ready 权重、可用于部署的不可变镜像摘要；
- 已准备一个可以在较短时间完成的 detect 训练产线和测试图片；
- 从 API 主机执行时，默认 API 地址为 `http://127.0.0.1:8000`。如果管理接口
  位于受保护代理之后，应把 `-PlatformApiBaseUri` 指向已授权的管理入口。

## 运行时镜像与网络边界

- x86 NVIDIA 节点只要求宿主机安装兼容的 NVIDIA 驱动、Docker 与 NVIDIA
  Container Toolkit。CUDA、TensorRT、PyTorch 和 Ultralytics 由不可变训练或
  推理镜像提供，不要求宿主机安装同版本 CUDA/TensorRT。
- 平台按 GPU compute capability、宿主驱动可支持的最高 CUDA 版本和架构选择
  兼容资源池；TensorRT 版本属于镜像身份，不参与 x86 宿主资源池兼容键。
- 部署和训练请求必须使用包含 registry、仓库名与 `sha256` 的完整镜像摘要。
  不接受可变 tag 作为生产执行身份。
- `VISIOX_MINIO_PUBLIC_URL` 必须配置为边缘节点可访问的 MinIO 地址。平台内部
  读写仍可使用 Compose 服务名；预签名下载与上传 URL 使用这个外部地址。
- 每次训练先把模型与数据集下载到远端任务目录。模型权重以只读方式挂载；
  数据集挂载的是任务独享副本，并允许 Ultralytics 写入图片修复结果与
  `labels/*.cache`；原始 MinIO 数据不会被修改。

边缘训练镜像复用同一套已验证的 CUDA/Ultralytics 推理基础镜像，只增加训练
入口，并移除继承的 HTTP 健康检查：

```powershell
docker build `
  -f workers/training-worker/Dockerfile.edge `
  --build-arg INFERENCE_IMAGE='registry.local/visiox/yolo26-inference@sha256:...' `
  -t registry.local/visiox/yolo26-training:release .
```

## 请求文件

服务请求文件遵循 `POST /services`，其中 `node_id`、`format` 和 `precision`
会由验收脚本覆盖。示例：

```json
{
  "name": "accept-x86-20260721",
  "pipeline_id": "PIPELINE_ID",
  "trained_model_id": "TRAINED_MODEL_ID",
  "model_name": "yolo26l",
  "model_weight": "best.pt",
  "environment": "edge-x86",
  "instance_name": "accept-x86-01",
  "resource_summary": "hardware acceptance",
  "node_id": "OVERWRITTEN_BY_SCRIPT",
  "image_digest": "registry.local/visiox-yolo26-inference@sha256:...",
  "model_checksum": "MODEL_SHA256",
  "port": 18080,
  "format": "engine",
  "precision": "fp16",
  "input_shape": [1, 3, 640, 640],
  "gpu_uuids": []
}
```

训练请求文件遵循 `POST /pipelines/{pipeline_id}/jobs`。为了让验收保持短小，
建议使用 1 至 2 个 epoch、较小图片尺寸和显式设备配置：

```json
{
  "params": {
    "epochs": 1,
    "batch": 2,
    "imgsz": 640,
    "workers": 2
  },
  "environment": {
    "device": "0"
  }
}
```

不要在这两个 JSON 文件中保存任何密码、私钥、Registry 密码或预签名 URL。

## 只生成待验收报告

不加 `-RunHardwareChecks` 时不会连接节点，也不会提示输入密码。结果必须为
`pending`，用于明确记录尚无物理设备证据：

```powershell
./scripts/accept-edge-x86.ps1 `
  -ExpectedHostFingerprint 'SHA256:EXPECTED'

./scripts/accept-edge-jetson.ps1 `
  -ExpectedHostFingerprint 'SHA256:EXPECTED'
```

此模式退出码为 0，但不代表硬件通过。不得把 `hardware_checks_executed=false`
或 `status=pending` 的报告标记为硬件验收成功。

## x86 NVIDIA GPU 验收

```powershell
./scripts/accept-edge-x86.ps1 `
  -RunHardwareChecks `
  -ExpectedHostFingerprint 'SHA256:EXPECTED' `
  -PlatformApiBaseUri 'http://127.0.0.1:8000' `
  -HostName '10.10.40.31' `
  -Administrator 'ubuntu' `
  -NodeName 'edge-x86-01' `
  -ServiceRequestJsonPath './acceptance/service-x86.json' `
  -TrainingPipelineId 'PIPELINE_ID' `
  -TrainingRequestJsonPath './acceptance/training-short.json' `
  -InferenceImagePath './acceptance/test.jpg' `
  -TimeoutSeconds 1800 |
  Tee-Object -FilePath './acceptance/x86-report.json'
```

## Jetson 验收

Jetson 首次 TensorRT 构建和短训练通常比 x86 慢，应为设备散热和磁盘空间留出
余量：

```powershell
./scripts/accept-edge-jetson.ps1 `
  -RunHardwareChecks `
  -ExpectedHostFingerprint 'SHA256:EXPECTED' `
  -PlatformApiBaseUri 'http://127.0.0.1:8000' `
  -HostName '10.10.40.41' `
  -Administrator 'ubuntu' `
  -NodeName 'edge-jetson-01' `
  -ServiceRequestJsonPath './acceptance/service-jetson.json' `
  -TrainingPipelineId 'PIPELINE_ID' `
  -TrainingRequestJsonPath './acceptance/training-short.json' `
  -InferenceImagePath './acceptance/test.jpg' `
  -TimeoutSeconds 3600 |
  Tee-Object -FilePath './acceptance/jetson-report.json'
```

加 `-KeepResources` 会保留已停止的测试服务以供排查；默认会在停止后删除测试
服务。训练产物作为验收证据保留，不会被脚本删除。

## JSON 与退出码

脚本最终向标准输出写入一个压缩 JSON 对象：

```json
{
  "schema_version": "visiox.edge-acceptance.v1",
  "platform": "x86_nvidia",
  "status": "passed",
  "hardware_checks_executed": true,
  "checks": []
}
```

- `passed`：全部已执行检查通过，退出码 0；
- `pending`：物理硬件流程没有运行，退出码 0，但不能作为通过证据；
- `failed`：至少一个检查失败，退出码 1。

只有同时满足以下条件的 JSON 才能作为硬件通过证据：

- `schema_version` 为 `visiox.edge-acceptance.v1`；
- `hardware_checks_executed` 为 `true`；
- 顶层 `status` 为 `passed`；
- 所有必需 check 均为 `passed`，或仅 cleanup 因显式 `-KeepResources` 为
  `skipped`。

## 故障定位

- `fingerprint_mismatch`：停止操作，从可信渠道重新核对指纹；不得绕过检查。
- bootstrap 409：节点已被引导或名称/地址冲突，按节点清理或密钥轮换流程处理。
- probe 不兼容：检查 Docker、NVIDIA Runtime、CUDA/TensorRT、JetPack/L4T
  版本和 GPU compute capability。
- TensorRT 导出失败：检查模型是否为 detect、镜像摘要、显存/磁盘空间及设备
  对应 TensorRT 版本。
- health 或推理失败：查看服务详情中的远程阶段、脱敏日志、容器 ID 与 endpoint。
- 训练或产物失败：查看训练任务日志、MinIO 连接和 ready 数据集/基础模型状态。

报告可能包含主机地址、节点 ID、服务 ID、镜像/引擎摘要等运维元数据，应按内部
发布证据管理，不要公开上传。
