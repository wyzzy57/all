# 数据准备模块实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 建立第一版数据准备模块，支持创建数据集、上传图片/zip、索引样本元数据、分配 train/val/test split、执行基础分析和任务类型格式校验 hook。

**架构：** 数据集和样本元数据写入 PostgreSQL，原始文件写入对象存储。API 只负责数据集和样本管理，数据分析与格式校验以同步 helper 和轻量 Task Center 任务入口提供，后续可迁移到 worker。Task 5 不接入 Label Studio、不生成 Ultralytics 训练目录、不实现 YOLO26 转换器。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2.x、Pillow、对象存储抽象、Task Center、pytest。

---

## 文件结构

- 修改：`pyproject.toml`，添加 `pillow` 依赖，并加入 `packages/visiox-yolo26/src/visiox_yolo26`。
- 修改：`apps/api-service/src/visiox_api/main.py`，注册 dataset route。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/__init__.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/datasets/__init__.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/datasets/analysis.py`，实现数据集分析 helper。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/datasets/validation.py`，实现任务类型格式校验 hook。
- 创建：`apps/api-service/src/visiox_api/routes/datasets.py`，实现数据集创建/查询/分析/校验 API。
- 创建：`apps/api-service/src/visiox_api/routes/dataset_samples.py`，实现样本上传、列表、split 分配 API。
- 创建：`tests/fixtures/datasets/`，放置最小图片/zip 测试 fixture。
- 创建：`tests/integration/test_dataset_upload.py`，覆盖数据集和样本上传工作流。

## 任务 1：先写失败的数据准备测试

- [ ] **步骤 1：创建 `tests/integration/test_dataset_upload.py`**

测试先写，必须在实现前失败。测试使用临时 SQLite + Alembic 创建表，使用 `InMemoryObjectStorageClient`，不要求真实 MinIO/Docker。

测试至少覆盖：

1. `POST /datasets`：
   - 创建 dataset。
   - 支持 `name`、`task`、`class_schema`。
   - `source` 默认为 `upload`，`status` 默认为 `created`。
2. `GET /datasets` 和 `GET /datasets/{id}`：
   - 支持 `task`、`status` 过滤。
3. `POST /datasets/{id}/samples:upload` 上传图片：
   - 接收 multipart file。
   - 写入对象存储。
   - 创建 `DatasetSample`。
   - 写入 `width`、`height`、`checksum`、`split="unassigned"`、`annotation_status="unlabeled"`。
   - 更新 `Dataset.sample_count`。
4. 上传 zip：
   - 解压图片文件并逐个建立样本。
   - 拒绝 zip slip 路径逃逸。
   - 跳过目录和非图片文件，或返回明确 validation error；计划中选择“跳过非图片文件并记录 skipped count”。
5. `GET /datasets/{id}/samples`：
   - 支持 `split`、`annotation_status` 过滤。
6. `POST /datasets/{id}/samples/splits`：
   - 按 sample id 分配 `train`、`val`、`test`。
   - 拒绝未知 sample id 或跨 dataset sample id。
7. `analyze_dataset(session, dataset_id)`：
   - 返回 sample count、class distribution、annotation count、image size distribution、empty annotation ratio、invalid samples。
8. `validate_dataset_format(session, dataset_id)`：
   - 对无样本数据集返回失败。
   - 对 classify 任务没有标注时给出 warning。
   - 对 detect/segment/semantic/pose/obb 没有标注时给出 error。
9. `POST /datasets/{id}/analyze` 和 `POST /datasets/{id}/validate`：
   - 创建 Task Center task，task_type 分别使用 `VALIDATE_DATASET_FORMAT` 或计划内合适的任务类型。
   - 第一版允许同步计算并把结果写入 task.payload。

- [ ] **步骤 2：运行测试并确认失败**

运行：

```bash
pytest tests/integration/test_dataset_upload.py -v
```

预期：失败，原因是 dataset route、sample route、analysis 和 validation 尚不存在。

## 任务 2：添加依赖和 YOLO26 package 入口

- [ ] **步骤 1：修改 `pyproject.toml`**

新增依赖：

```toml
  "pillow>=10.0,<12.0",
```

新增 wheel package：

```toml
  "packages/visiox-yolo26/src/visiox_yolo26",
```

新增 pytest pythonpath：

```toml
  "packages/visiox-yolo26/src",
```

- [ ] **步骤 2：创建 package init**

创建：

- `packages/visiox-yolo26/src/visiox_yolo26/__init__.py`
- `packages/visiox-yolo26/src/visiox_yolo26/datasets/__init__.py`

内容保持简短 docstring。

## 任务 3：实现分析与格式校验 helper

- [ ] **步骤 1：创建 `packages/visiox-yolo26/src/visiox_yolo26/datasets/analysis.py`**

实现：

- `DatasetAnalysisResult` Pydantic model 或 dataclass。
- `analyze_dataset(session, dataset_id) -> dict[str, object]`。

返回字段至少包括：

```json
{
  "sample_count": 0,
  "class_distribution": {},
  "annotation_count": 0,
  "image_size_distribution": {
    "total_with_dimensions": 0,
    "by_size": {}
  },
  "empty_annotation_ratio": 1.0,
  "invalid_samples": []
}
```

说明：

- Task 5 尚未实现 annotation import，`class_distribution` 可基于 `Annotation.internal_payload` 中已有的 `class_name` 或 `class_id` 做 best-effort 统计。
- 没有 annotation 时 `empty_annotation_ratio=1.0`（有样本）或 `0.0`（无样本）。

- [ ] **步骤 2：创建 `packages/visiox-yolo26/src/visiox_yolo26/datasets/validation.py`**

实现：

- `DatasetValidationResult`。
- `validate_dataset_format(session, dataset_id) -> dict[str, object]`。

规则：

- dataset 不存在抛明确错误。
- 无样本：`valid=false`，error `DATASET_EMPTY`。
- 任务不在 `detect | segment | semantic | pose | obb | classify`：`valid=false`，error `UNSUPPORTED_TASK`。
- 有 invalid sample：`valid=false`，error `INVALID_SAMPLE`。
- `classify` 没有 annotation：warning `CLASSIFY_LABELS_MISSING`。
- 其他视觉任务没有 annotation：`valid=false`，error `ANNOTATIONS_MISSING`。

## 任务 4：实现数据集 API

- [ ] **步骤 1：创建 `apps/api-service/src/visiox_api/routes/datasets.py`**

实现：

- `POST /datasets`
- `GET /datasets`
- `GET /datasets/{dataset_id}`
- `POST /datasets/{dataset_id}/analyze`
- `POST /datasets/{dataset_id}/validate`

要求：

- 创建数据集时校验 task 属于六类任务。
- `class_schema` 默认为 `{}`。
- `source` 默认为 `upload`。
- `analyze` 同步调用 `analyze_dataset()`，创建 Task 记录，状态 `SUCCESS`，payload 包含结果。
- `validate` 同步调用 `validate_dataset_format()`，创建 Task 记录，状态根据结果为 `SUCCESS` 或 `FAILED`，payload 包含结果，失败时设置 `error_code`。

## 任务 5：实现样本上传和 split 分配 API

- [ ] **步骤 1：创建 `apps/api-service/src/visiox_api/routes/dataset_samples.py`**

实现：

- `POST /datasets/{dataset_id}/samples:upload`
- `GET /datasets/{dataset_id}/samples`
- `POST /datasets/{dataset_id}/samples/splits`

上传要求：

- 支持图片：`.jpg`、`.jpeg`、`.png`、`.bmp`、`.webp`。
- 支持 zip：逐个提取安全路径下的图片文件。
- 单文件图片 object key：`datasets/{dataset_id}/samples/{checksum}-{safe_filename}`。
- zip 内图片 object key：同上，filename 使用 zip entry basename。
- 使用 Pillow 读取图片尺寸。
- 使用 `sha256_bytes` 或 `sha256_file` 计算 checksum。
- `DatasetSample.split="unassigned"`。
- `DatasetSample.annotation_status="unlabeled"`。
- 更新 `Dataset.sample_count`。
- 拒绝 zip slip：任何 absolute path、`..` path、路径解压后不在临时目录内的 entry 直接报错。
- 同一 dataset 内 checksum 重复时不要重复创建样本，返回已有样本并记录 duplicate count。

对象存储 dependency：

- 默认可先用 app state 中的 storage client；如果不存在，在测试中 override。
- 不要在本任务强制真实 MinIO。

- [ ] **步骤 2：修改 `apps/api-service/src/visiox_api/main.py`**

注册：

- `datasets_router`
- `dataset_samples_router`

## 任务 6：添加测试 fixture

- [ ] **步骤 1：创建 `tests/fixtures/datasets/README.md`**

说明测试 fixture 由测试代码动态生成，避免提交二进制图片。

要求：

- 不提交大文件。
- 测试用 Pillow 动态生成小图片和 zip。

## 任务 7：验证和提交

- [ ] **步骤 1：安装依赖**

运行：

```bash
python -m pip install -e ".[test,dev]"
```

- [ ] **步骤 2：运行数据准备测试**

运行：

```bash
pytest tests/integration/test_dataset_upload.py -v
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
git add pyproject.toml apps/api-service/src/visiox_api packages/visiox-yolo26 tests/fixtures/datasets tests/integration/test_dataset_upload.py docs/superpowers/plans/2026-07-03-data-preparation.md
git commit -m "feat: add data preparation module"
```

## 自查

- 规格覆盖：本计划覆盖 Task 5 要求的数据上传、数据集管理、样本管理、数据分析、格式检验 hook 和基础测试。
- 有意延后：不实现 Label Studio 项目创建/同步、不实现 annotation import、不生成 Ultralytics 训练目录、不实现 YOLO26 converter、不实现异步数据分析 worker。
- 对象存储：测试使用 in-memory storage；真实 MinIO 行为由 Task 4 的 storage wrapper 负责。
- 文档语言：本文档正文使用中文；代码、路径、命令、API、状态值和字段名保持英文。
