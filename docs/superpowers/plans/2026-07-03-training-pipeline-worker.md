# 训练产线与 Training Worker 实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 支持创建训练产线、校验训练参数、提交 `TRAIN_MODEL` 任务，并由训练 worker 导出 YOLO26 数据集、构建训练命令、采集日志/指标、登记 `TrainedModel` 版本。

**架构：** API 服务负责 pipeline/job CRUD、预检查和任务提交；`training-worker` 负责实际训练流程。第一版 worker 使用可注入 command runner，测试中用 fake runner，不依赖真实 Ultralytics、GPU 或 Docker。Task 8 不做模型格式导出、边缘应用打包、部署或推理服务，这些留给后续任务。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2.x、Redis Task Center、对象存储抽象、YOLO26 dataset exporter、pytest。

---

## 文件结构

- 修改：`pyproject.toml`，加入 `workers/training-worker/src` 到 wheel package 和 pytest pythonpath。
- 修改：`apps/api-service/src/visiox_api/main.py`，注册 pipeline 和 training job route。
- 创建：`apps/api-service/src/visiox_api/routes/pipelines.py`。
- 创建：`apps/api-service/src/visiox_api/routes/training_jobs.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/training/__init__.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/training/params.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/training/commands.py`。
- 创建：`workers/training-worker/src/visiox_training_worker/__init__.py`。
- 创建：`workers/training-worker/src/visiox_training_worker/main.py`。
- 创建：`tests/integration/test_training_pipeline.py`。

## 范围边界

- 本任务允许创建 `TrainingPipeline`、`TrainingJob`、`TrainedModel`。
- 本任务允许调用 Task 7 的 `export_yolo26_dataset`。
- 本任务只生成训练命令和登记训练结果，不实现 Task 9 的 ONNX/TensorRT/openvino 导出。
- 本任务不构建 edge app package，不写 deployment worker，不改前端。
- 第一版 worker 不强制实现长驻 Redis consumer；可提供纯函数，后续再接入实际消费循环。

## 任务 1：先写失败的训练产线测试

- [ ] **步骤 1：创建 `tests/integration/test_training_pipeline.py`**

测试先写，必须在实现前失败。测试使用临时 SQLite + Alembic、`InMemoryObjectStorageClient`、fake stream producer、fake command runner。

测试至少覆盖：

1. `POST /pipelines`：
   - 创建 pipeline。
   - 引用 ready base model 和已校验 dataset。
   - 写入 `task`、`scale`、`params_template`、`default_environment`。
   - 拒绝 task/scale 和 base model 不匹配。
   - 拒绝 base model 非 `ready`。
   - 拒绝 dataset task 不匹配或没有样本/标注。
2. `GET /pipelines` 和 `GET /pipelines/{id}`：
   - 支持 `task`、`status` 过滤。
3. 参数校验：
   - 支持白名单字段：`epochs`、`batch`、`imgsz`、`lr0`、`patience`、`workers`、`device`、`seed`。
   - 拒绝未知参数、负数、过大值和不支持的环境字段。
4. `POST /pipelines/{id}/jobs`：
   - 创建 `TrainingJob`。
   - 创建并 enqueue `TRAIN_MODEL` Task。
   - payload 包含 `pipeline_id`、`training_job_id`、`dataset_id`、`base_model_id`、`params`、`environment`。
   - enqueue 失败时 task/job 都标记失败。
5. worker 成功流：
   - `run_training_job(session, storage, runner, task_id, training_job_id, work_dir)`。
   - 调用 `export_yolo26_dataset`。
   - 构建 YOLO train 命令。
   - fake runner 返回 metrics 和 artifact path。
   - artifact 写入对象存储。
   - 创建 `TrainedModel`。
   - 更新 `TrainingJob.trained_model_id`、`metrics`、`log_uri`、`status`。
   - Task 标记 `SUCCESS`。
6. worker 失败流：
   - command runner 抛错时，task/job 标记失败。
   - 写入 stage、error code、error message、retryable。
   - 不创建 `TrainedModel`。
7. 防重复运行：
   - 已 terminal 的 task 不应重复训练。
   - training job 已成功时返回已有模型或抛出明确错误；计划中选择“返回已有模型并保持幂等”。

- [ ] **步骤 2：运行测试并确认失败**

运行：

```bash
pytest tests/integration/test_training_pipeline.py -v
```

预期：失败，原因是 routes、training package 和 worker 尚不存在。

## 任务 2：实现训练参数和命令构建

- [ ] `params.py`：
  - 定义 `TrainingParams`。
  - 定义 `TrainingEnvironment`。
  - 提供 `validate_training_params(payload)`。
  - 提供 `merge_training_params(template, override)`。
  - 拒绝未知字段和越界值。
- [ ] `commands.py`：
  - 定义 `YoloTrainCommand` dataclass。
  - 提供 `build_train_command(base_model_path, data_yaml_path, params, project_dir, run_name)`。
  - 输出参数列表，不拼 shell 字符串。
  - 命令第一版使用 `yolo` CLI 参数形式，测试只验证列表。

## 任务 3：实现 pipeline API

- [ ] `POST /pipelines`：
  - 校验 task 和 scale 属于 YOLO26 注册表。
  - 校验 base model 存在、`status="ready"`、task/scale 匹配。
  - 校验 dataset 存在、task 匹配、有样本、有 annotation。
  - 校验 params/environment。
  - 创建 `TrainingPipeline(status="ready")`。
- [ ] `GET /pipelines` 和 `GET /pipelines/{id}`。
- [ ] 第一版不实现 update/delete，避免训练引用对象被改坏。

## 任务 4：实现 training job API

- [ ] `POST /pipelines/{pipeline_id}/jobs`：
  - 校验 pipeline ready。
  - 合并 params 和 environment override。
  - 创建 `TrainingJob(status="queued")`。
  - 创建 Task：`task_type=TRAIN_MODEL`、`resource_type="training_job"`。
  - enqueue 到 `stream:training.commands`。
  - enqueue 失败时 task/job 均标记 failed。
- [ ] `GET /training-jobs`：
  - 支持 `pipeline_id`、`status` 过滤。
- [ ] `GET /training-jobs/{id}`。

## 任务 5：实现 Training Worker 纯函数

- [ ] 创建 `run_training_job(session, storage, runner, task_id, training_job_id, work_dir)`。
- [ ] 校验 task 类型和 resource/payload。
- [ ] 若 task/job terminal，幂等返回或抛明确错误。
- [ ] 读取 pipeline/base model/dataset。
- [ ] 下载 base model artifact 到工作目录。
- [ ] 使用 Task 7 导出 YOLO26 数据集。
- [ ] 调用 command runner。
- [ ] 采集 metrics、stdout/stderr/log。
- [ ] 将训练 artifact 和 log 写入对象存储。
- [ ] 创建 `TrainedModel` 版本，命名可用 `pipeline.name` + job id 前缀。
- [ ] 更新 job/task 成功状态。

## 任务 6：失败与一致性加固

- [ ] worker 失败时：
  - job.status=`failed`。
  - task.status=`FAILED`。
  - task.stage 保留失败阶段。
  - task.error_code=`TRAINING_FAILED` 或更具体 code。
  - task.retryable=True。
- [ ] 对象存储写入与 DB commit 失败时，记录可追踪错误；第一版不要求删除已写训练 artifact，但测试要覆盖训练命令失败时不写 artifact。
- [ ] 禁止训练命令通过 shell 字符串执行，runner 接收 argv list。

## 任务 7：验证与提交

- [ ] 运行：

```bash
pytest tests/integration/test_training_pipeline.py -v
pytest -v -p no:cacheprovider --basetemp=.tmp_pytest_training_20260704
ruff check apps packages tests infra workers
```

- [ ] 清理 `.tmp_pytest*` 临时目录，不提交测试产物。
- [ ] 提交为 `feat: add training pipeline worker`。

## 验收标准

- 基础模型、数据集、参数和环境未通过预检查时不能开始训练。
- 提交训练会创建 `TrainingJob` 和 `TRAIN_MODEL` Task。
- worker 成功后创建 `TrainedModel`，并登记 artifact URI、metrics、log URI。
- 失败时记录 stage、error code 和 retry flag。
- 不调用真实 Ultralytics，不实现模型导出/边缘应用包。
