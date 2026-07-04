# YOLO26 数据集转换器实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 将平台内部 `Dataset`、`DatasetSample` 和 `Annotation.internal_payload` 转换为六类 YOLO26/Ultralytics 数据集目录：`detect`、`segment`、`semantic`、`pose`、`obb`、`classify`。输出必须确定性，便于训练 worker 直接消费。

**架构：** Task 7 只实现纯 Python 转换库和 fixture 测试，不提交训练任务、不运行 Ultralytics、不接入 Redis worker。转换入口读取数据库 session 和对象存储抽象，把样本文件复制到输出目录并生成 label/mask/data.yaml。Label Studio 已在 Task 6 规范化为内部 payload，本任务只消费内部 schema。

**技术栈：** Python 3.12、SQLAlchemy 2.x、Pillow、对象存储抽象、pytest。

---

## 文件结构

- 创建：`packages/visiox-yolo26/src/visiox_yolo26/tasks.py`，定义六类任务和五个尺度的注册表。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/__init__.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/internal_schema.py`，定义内部 annotation 解析、错误和公共工具。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/detect.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/segment.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/semantic.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/pose.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/obb.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/classify.py`。
- 创建：`packages/visiox-yolo26/src/visiox_yolo26/converters/exporter.py`，提供统一导出入口。
- 创建：`tests/fixtures/yolo26_expected/*`，保存确定性期望输出。
- 创建：`tests/integration/test_yolo26_converters.py`，覆盖六类任务转换。

## 内部 annotation 输入约定

转换器消费 `Annotation.internal_payload` 中的 `annotations[].results[]`。每个 result 可包含：

- `class_name` 或 `class_id`。
- `shape`：
  - `rectangle`：`x`、`y`、`width`、`height`，百分比坐标。
  - `polygon`：`points`，百分比坐标点列表。
  - `brush`：第一版仅支持已解码 `mask` 或 `points`，不实现 Label Studio RLE 完整解码；遇到 `rle` 返回可操作 validation error。
  - `keypoints`：支持单点 result，按 `keypoint_name` 或顺序归并到 COCO 17 点。
  - `classification`：使用 `class_name`/`class_id` 选择分类目录。
- `source_result_id` 用于错误定位。

坐标在 Task 6 中保留 Label Studio 百分比值；Task 7 转为 YOLO 归一化浮点或像素 mask。

## 任务 1：先写失败的转换器测试

- [ ] **步骤 1：创建 `tests/integration/test_yolo26_converters.py`**

测试先写，必须在实现前失败。测试使用临时 SQLite + Alembic、`InMemoryObjectStorageClient`，动态创建最小图片并写入对象存储。

测试至少覆盖：

1. 任务注册表：
   - 六类任务：`detect`、`segment`、`semantic`、`pose`、`obb`、`classify`。
   - 五个尺度：`n`、`s`、`m`、`l`、`x`。
2. `detect`：
   - rectangle 转 `class_id x_center y_center width height`。
   - 百分比坐标正确归一化。
3. `segment`：
   - polygon 转 `class_id x1 y1 x2 y2 ...`。
4. `semantic`：
   - polygon/brush rasterize 为 mask PNG。
   - 重叠区域按确定性顺序覆盖并产生 warning。
5. `pose`：
   - COCO 17 点输出 `class_id bbox keypoints...`。
   - 缺失点填 `0 0 0`。
6. `obb`：
   - 四点 polygon 转 `class_id x1 y1 x2 y2 x3 y3 x4 y4`。
   - rectangle 可转为四点。
7. `classify`：
   - 样本复制到 `train/<class_name>/...` 等目录。
8. `data.yaml`：
   - 每类任务生成稳定 YAML，包含 path/train/val/test/names/task。
9. 错误：
   - 未知 class、缺尺寸、无效坐标、缺 annotation 产生 `ConversionError`，错误信息包含 dataset/sample/source_result_id。
10. 输出确定性：
   - 与 `tests/fixtures/yolo26_expected/*` 逐字节匹配，或使用测试内 snapshot 字符串逐字节比较。

- [ ] **步骤 2：运行测试并确认失败**

运行：

```bash
pytest tests/integration/test_yolo26_converters.py -v
```

预期：失败，原因是 converters package 尚不存在。

## 任务 2：实现任务注册表和公共 schema

- [ ] `visiox_yolo26.tasks`：
  - 定义 `YOLO26_TASKS` 和 `YOLO26_SCALES`。
  - 定义 `task_scale_key(task, scale)` 或等价 helper。
- [ ] `internal_schema.py`：
  - 定义 `ConversionError`、`ConversionWarning`。
  - 解析 class schema，将 `class_name` 映射到 class id。
  - 展平 `Annotation.internal_payload` 为统一 result 列表。
  - 校验百分比坐标范围。
  - 提供确定性浮点格式化，默认 6 位小数，去掉多余尾零。

## 任务 3：实现统一导出入口

- [ ] `exporter.py` 提供 `export_yolo26_dataset(session, storage, dataset_id, output_dir) -> ConversionReport`。
- [ ] 根据 `Dataset.task` 分派到具体转换器。
- [ ] 按 split 输出 `train`、`val`、`test`，缺失 split 时使用 `train`。
- [ ] 从对象存储读取样本文件到输出目录。
- [ ] 返回写入文件列表、warning 列表、样本数、annotation 数。

## 任务 4：实现 detect/segment/obb/classify

- [ ] `detect.py`：
  - 支持 rectangle。
  - 输出 `images/<split>/` 和 `labels/<split>/`。
- [ ] `segment.py`：
  - 支持 polygon。
  - 输出 polygon label。
- [ ] `obb.py`：
  - 支持 polygon 四点和 rectangle。
  - 输出 OBB 四点 label。
- [ ] `classify.py`：
  - 支持 classification result。
  - 输出 `<split>/<class_name>/image`。

## 任务 5：实现 semantic mask rasterization

- [ ] `semantic.py`：
  - 支持 polygon rasterization 到单通道 PNG mask。
  - mask 像素值使用 class id + 1，背景为 0。
  - 多个 polygon 重叠时按 annotation/result 稳定顺序后者覆盖前者，并记录 warning。
  - 对 brush RLE 暂不支持，返回明确 `ConversionError`，避免静默错误。

## 任务 6：实现 pose

- [ ] `pose.py`：
  - 使用 COCO 17 点顺序。
  - 支持 keypoints result 中的 `keypoint_name`。
  - 计算 bbox 或从 rectangle result 读取 bbox。
  - 缺失点输出 `0 0 0`，存在点输出 `x y 2`。

## 任务 7：验证与提交

- [ ] 运行：

```bash
pytest tests/integration/test_yolo26_converters.py -v
pytest -v -p no:cacheprovider --basetemp=.tmp_pytest_base_20260704
ruff check apps packages tests infra workers
```

- [ ] 若默认 pytest 临时目录在 Windows 上权限异常，使用工作区下 `--basetemp`，但不要把临时目录提交；`.gitignore` 已忽略 `.tmp_pytest*/`。
- [ ] 提交为 `feat: add yolo26 dataset converters`。

## 验收标准

- 六类任务均可从内部 annotation 导出确定性 YOLO26 数据集目录。
- `data.yaml` 稳定生成。
- 无效 annotation 给出可操作 `ConversionError`。
- semantic 重叠 mask 按确定性顺序覆盖并产生 warning。
- 不启动训练、不调用 Ultralytics、不修改训练 worker。
