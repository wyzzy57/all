# 统一 YOLO26 推理服务执行计划

**任务编号：** Task 11

**目标：** 提供一个统一的 YOLO26 推理服务镜像，通过 runtime 配置加载不同任务模型，向 Edge App 提供稳定 HTTP 推理接口。

**架构：** `apps/yolo26-inference` 是边缘应用包引用的推理服务。第一版不强制安装真实 Ultralytics/GPU 依赖，而是提供可注入 predictor 抽象；生产环境后续可替换为真实 YOLO26 predictor，测试使用 deterministic fake predictor。

**技术栈：** Python 3.12、FastAPI、Pydantic、Pillow、pytest、Docker。

## 范围

- 新增 `apps/yolo26-inference` 服务包。
- 新增 runtime 配置解析和校验。
- 支持任务类型：
  - `detect`
  - `segment`
  - `semantic`
  - `pose`
  - `obb`
  - `classify`
- 实现 API：
  - `GET /health`
  - `GET /model/info`
  - `POST /predict/image`
  - `POST /predict/video-frame`
  - `POST /runtime/reload`
  - `GET /metrics`
- 新增 Dockerfile。
- 新增集成测试。

## 非目标

- 不引入真实 Ultralytics 依赖。
- 不实现 GPU/TensorRT/OpenVINO 加速。
- 不拉 RTSP 流。
- 不写部署 worker 或 Edge Agent 逻辑。
- 不改前端。

## Runtime 配置

默认配置来源：

- 环境变量 `VISIOX_INFERENCE_CONFIG` 指向 JSON 配置文件。
- 如果未提供配置文件，使用内置默认配置。

配置结构：

```json
{
  "task": "detect",
  "model_path": "/app/model/exported.onnx",
  "model_format": "onnx",
  "device": "cpu",
  "confidence": 0.25,
  "iou": 0.7,
  "class_names": ["defect"],
  "input": {
    "type": "http"
  }
}
```

校验规则：

- `task` 必须属于 YOLO26 六类任务。
- `model_format` 必须属于 `onnx`、`torchscript`、`pt`。
- 如果 `model_path` 存在任务提示，例如 `detect.onnx`，与 `task` 不匹配时快速失败。
- `confidence` 和 `iou` 必须在 `[0, 1]`。

## 响应约定

### `GET /health`

返回服务健康、当前 task、模型加载状态。

### `GET /model/info`

返回 task、model_path、model_format、device、class_names、loaded_at。

### `POST /predict/image`

接收 multipart image，返回统一结构：

```json
{
  "task": "detect",
  "predictions": [],
  "latency_ms": 1.2,
  "image": {
    "width": 640,
    "height": 480
  }
}
```

### `POST /predict/video-frame`

与 image 相同，额外接受 `camera_id` 和 `timestamp_ms`。

### `POST /runtime/reload`

接收可选配置覆盖，重新加载 predictor。

### `GET /metrics`

返回请求计数、错误计数、最近一次延迟和启动时间。

## 文件变更

### 1. `apps/yolo26-inference/src/visiox_yolo26_inference/config.py`

- 定义 `InferenceConfig`。
- 定义 `load_config(path=None)`。
- 定义 `validate_model_task_match(config)`。

### 2. `apps/yolo26-inference/src/visiox_yolo26_inference/predict.py`

- 定义 `PredictionResult`。
- 定义 `Predictor` 协议。
- 定义 `DeterministicPredictor`，按 task 返回稳定空/示例预测。
- 定义 `load_predictor(config)`。

### 3. `apps/yolo26-inference/src/visiox_yolo26_inference/main.py`

- 创建 FastAPI app。
- 维护 app state：config、predictor、metrics。
- 实现六个 API。
- 支持测试注入 config/predictor。

### 4. `apps/yolo26-inference/Dockerfile`

- 基于 Python slim。
- 安装当前 wheel。
- 暴露 8080。
- 启动 `uvicorn visiox_yolo26_inference.main:app`。

### 5. `pyproject.toml`

- 加入 `apps/yolo26-inference/src/visiox_yolo26_inference`。
- 加入 pytest `pythonpath`。

### 6. `tests/integration/test_yolo26_inference_api.py`

- 覆盖 health、model info、image predict、video-frame predict、runtime reload、metrics。
- 覆盖六类 task 可加载。
- 覆盖 task/model mismatch 快速失败。

## 实施步骤

1. 创建本计划并提交。
2. 实现 config/predict/main/Dockerfile。
3. 更新 `pyproject.toml`。
4. 增加集成测试。
5. 运行聚焦测试、全量测试和 ruff。
6. 修复评审问题。
7. 提交为 `feat: add yolo26 inference service`。

## 验收标准

- 同一个服务可以加载 YOLO26 六类任务配置。
- 图片和视频帧预测接口返回稳定结构。
- model/task 不匹配时返回清晰错误。
- metrics 可以反映请求和错误计数。
- `pytest` 和 `ruff check` 通过。
