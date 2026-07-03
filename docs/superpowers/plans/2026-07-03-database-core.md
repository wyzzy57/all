# 数据库核心、迁移和种子数据实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 为 Visiox MVP 建立第一版数据库核心 schema、Alembic 迁移环境和 YOLO26 基础模型种子数据。

**架构：** 新增独立 `packages/visiox-db` 包，集中放置 SQLAlchemy 2.x declarative model、session 工厂和 metadata。Alembic 使用同一份 metadata 生成/执行迁移；本地测试用临时 SQLite 验证空库 upgrade，生产配置仍面向 PostgreSQL。种子数据先以 JSON 文件交付，后续任务再接入实际 seed runner。

**技术栈：** Python 3.12、SQLAlchemy 2.x、Alembic、pytest、PostgreSQL 兼容 schema、SQLite 临时迁移测试。

---

## 文件结构

- 修改：`pyproject.toml`，添加 `sqlalchemy`、`alembic` 依赖，并把 `packages/visiox-db/src/visiox_db` 加入 wheel packages 和 pytest pythonpath。
- 创建：`packages/visiox-db/src/visiox_db/__init__.py`，导出数据库包基础接口。
- 创建：`packages/visiox-db/src/visiox_db/base.py`，定义 `Base`、通用 id/timestamp mixin 和 metadata 命名约定。
- 创建：`packages/visiox-db/src/visiox_db/session.py`，定义 engine 和 session factory。
- 创建：`packages/visiox-db/src/visiox_db/models/__init__.py`，集中导入全部 model，保证 Alembic 能发现 metadata。
- 创建：`packages/visiox-db/src/visiox_db/models/tasks.py`，定义任务中心 `Task`。
- 创建：`packages/visiox-db/src/visiox_db/models/model_space.py`，定义 `ModelSource`、`BaseModel`、`TrainedModel`、`TrainingPipeline`、`TrainingJob`。
- 创建：`packages/visiox-db/src/visiox_db/models/datasets.py`，定义 `Dataset`、`DatasetSample`、`Annotation`、`LabelProject`。
- 创建：`packages/visiox-db/src/visiox_db/models/edge.py`，定义 `Device`、`Camera`、`EdgeApp`、`EdgeAppVersion`、`Deployment`。
- 创建：`alembic.ini`，提供 Alembic 默认配置。
- 创建：`infra/migrations/env.py`，配置 Alembic metadata 和 URL 来源。
- 创建：`infra/migrations/script.py.mako`，提供 migration 模板。
- 创建：`infra/migrations/versions/20260703_0001_database_core.py`，创建第一版核心表。
- 创建：`infra/seed/yolo26_base_models.json`，写入 6 类任务 × 5 个尺度，共 30 条 YOLO26 基础模型元数据。
- 创建：`tests/integration/test_migrations.py`，验证迁移和种子数据。

## 任务 1：先写失败的迁移和种子测试

- [ ] **步骤 1：创建 `tests/integration/test_migrations.py`**

测试必须先写，并在实现前失败。测试内容：

```python
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


EXPECTED_TABLES = {
    "tasks",
    "model_sources",
    "base_models",
    "trained_models",
    "training_pipelines",
    "training_jobs",
    "datasets",
    "dataset_samples",
    "annotations",
    "label_projects",
    "devices",
    "cameras",
    "edge_apps",
    "edge_app_versions",
    "deployments",
}


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_alembic_upgrade_creates_database_core_tables(tmp_path):
    database_path = tmp_path / "visiox.db"
    database_url = f"sqlite:///{database_path}"

    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())

    assert EXPECTED_TABLES.issubset(table_names)
    assert {"users", "roles", "permissions"}.isdisjoint(table_names)


def test_yolo26_base_model_seed_has_all_tasks_and_scales():
    seed_path = Path("infra/seed/yolo26_base_models.json")

    records = json.loads(seed_path.read_text(encoding="utf-8"))

    assert len(records) == 30
    assert {record["task"] for record in records} == {
        "detect",
        "segment",
        "semantic",
        "pose",
        "obb",
        "classify",
    }
    assert {record["scale"] for record in records} == {"n", "s", "m", "l", "x"}
    assert len({record["id"] for record in records}) == 30
    assert len({record["filename"] for record in records}) == 30
```

- [ ] **步骤 2：运行测试并确认失败**

运行：

```bash
pytest tests/integration/test_migrations.py -v
```

预期：失败，原因是 `alembic`、`alembic.ini`、迁移文件和 seed 文件尚不存在。

## 任务 2：添加依赖和数据库包入口

- [ ] **步骤 1：修改 `pyproject.toml`**

在 `[project].dependencies` 中加入：

```toml
  "alembic>=1.13,<2.0",
  "sqlalchemy>=2.0,<3.0",
```

在 `[tool.hatch.build.targets.wheel].packages` 中加入：

```toml
  "packages/visiox-db/src/visiox_db",
```

在 `[tool.pytest.ini_options].pythonpath` 中加入：

```toml
  "packages/visiox-db/src",
```

- [ ] **步骤 2：创建数据库包入口**

创建 `packages/visiox-db/src/visiox_db/__init__.py`：

```python
"""Visiox database package."""
```

## 任务 3：实现 SQLAlchemy 基础设施

- [ ] **步骤 1：创建 `packages/visiox-db/src/visiox_db/base.py`**

```python
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, String


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
```

- [ ] **步骤 2：创建 `packages/visiox-db/src/visiox_db/session.py`**

```python
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_common.settings import get_settings


def create_db_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(database_url or settings.postgres_dsn, pool_pre_ping=True)


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or create_db_engine(), autoflush=False, expire_on_commit=False)


SessionLocal = create_session_factory()


def get_session() -> Generator[Session]:
    with SessionLocal() as session:
        yield session
```

## 任务 4：实现核心 model

- [ ] **步骤 1：创建 `packages/visiox-db/src/visiox_db/models/tasks.py`**

`Task` 表名为 `tasks`。字段至少包括：

- `task_type`：`String(80)`，非空，建索引。
- `status`：`String(24)`，非空，默认 `PENDING`，建索引。
- `progress`：`Integer`，默认 `0`。
- `resource_type`：`String(80)`，可空。
- `resource_id`：`String(36)`，可空。
- `stage`：`String(120)`，可空。
- `payload`：`JSON`，默认 `{}`。
- `error_code`：`String(80)`，可空。
- `error_message`：`Text`，可空。
- `retryable`：`Boolean`，默认 `False`。
- `started_at`、`finished_at`：`DateTime(timezone=True)`，可空。

- [ ] **步骤 2：创建 `packages/visiox-db/src/visiox_db/models/model_space.py`**

需要定义：

- `ModelSource` -> `model_sources`
  - `name`、`type`、`base_url`、`bucket`、`mount_path`、`enabled`
- `BaseModel` -> `base_models`
  - `family`、`task`、`scale`、`filename`、`source_path`、`local_uri`、`checksum`、`size_bytes`、`status`、`model_source_id`
  - `family/task/scale` 加唯一约束
- `TrainingPipeline` -> `training_pipelines`
  - `name`、`task`、`scale`、`base_model_id`、`dataset_id`、`params_template`、`default_environment`、`status`
- `TrainingJob` -> `training_jobs`
  - `pipeline_id`、`task_id`、`trained_model_id`、`status`、`params`、`metrics`、`log_uri`、`started_at`、`finished_at`
- `TrainedModel` -> `trained_models`
  - `pipeline_id`、`training_job_id`、`name`、`version`、`task`、`artifact_uri`、`metrics`、`status`

外键可以先只保留数据库约束，不要求配置复杂 relationship。

- [ ] **步骤 3：创建 `packages/visiox-db/src/visiox_db/models/datasets.py`**

需要定义：

- `Dataset` -> `datasets`
  - `name`、`task`、`status`、`class_schema`、`sample_count`、`annotation_count`、`source`、`storage_uri`
- `DatasetSample` -> `dataset_samples`
  - `dataset_id`、`file_uri`、`width`、`height`、`checksum`、`split`、`annotation_status`
- `Annotation` -> `annotations`
  - `dataset_sample_id`、`source`、`raw_payload_uri`、`internal_payload`、`validation_status`
- `LabelProject` -> `label_projects`
  - `dataset_id`、`provider`、`external_project_id`、`sync_status`、`last_sync_at`

- [ ] **步骤 4：创建 `packages/visiox-db/src/visiox_db/models/edge.py`**

需要定义：

- `Device` -> `devices`
  - `name`、`endpoint_url`、`status`、`token_ref`、`last_heartbeat_at`、`resource_info`
- `Camera` -> `cameras`
  - `device_id`、`name`、`rtsp_url`、`status`、`last_snapshot_uri`
- `EdgeApp` -> `edge_apps`
  - `name`、`description`、`status`
- `EdgeAppVersion` -> `edge_app_versions`
  - `edge_app_id`、`trained_model_id`、`version`、`package_uri`、`manifest`、`checksum`、`status`
- `Deployment` -> `deployments`
  - `device_id`、`edge_app_version_id`、`task_id`、`status`、`active`、`deployed_at`、`stopped_at`、`logs_uri`

- [ ] **步骤 5：创建 `packages/visiox-db/src/visiox_db/models/__init__.py`**

集中导入全部 model：

```python
from visiox_db.models.datasets import Annotation, Dataset, DatasetSample, LabelProject
from visiox_db.models.edge import Camera, Deployment, Device, EdgeApp, EdgeAppVersion
from visiox_db.models.model_space import BaseModel, ModelSource, TrainedModel, TrainingJob, TrainingPipeline
from visiox_db.models.tasks import Task

__all__ = [
    "Annotation",
    "BaseModel",
    "Camera",
    "Dataset",
    "DatasetSample",
    "Deployment",
    "Device",
    "EdgeApp",
    "EdgeAppVersion",
    "LabelProject",
    "ModelSource",
    "Task",
    "TrainedModel",
    "TrainingJob",
    "TrainingPipeline",
]
```

## 任务 5：添加 Alembic 环境和初始迁移

- [ ] **步骤 1：创建 `alembic.ini`**

关键配置：

```ini
[alembic]
script_location = infra/migrations
prepend_sys_path = .
sqlalchemy.url = postgresql+psycopg://visiox:visiox@postgres:5432/visiox

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic
```

其余 logging 配置按 Alembic 标准模板补齐。

- [ ] **步骤 2：创建 `infra/migrations/env.py`**

必须：

- 把 `apps/api-service/src`、`packages/visiox-common/src`、`packages/visiox-db/src` 加入 `sys.path`。
- 导入 `visiox_db.models`，确保所有 model 注册到 metadata。
- 使用 `Base.metadata` 作为 `target_metadata`。
- 优先读取 Alembic config 的 `sqlalchemy.url`，没有时使用 `VISIOX_POSTGRES_DSN`。
- 支持 offline 和 online migration。

- [ ] **步骤 3：创建 `infra/migrations/script.py.mako`**

使用 Alembic 标准模板，包含 `upgrade()` 和 `downgrade()`。

- [ ] **步骤 4：创建 `infra/migrations/versions/20260703_0001_database_core.py`**

迁移必须创建以下表：

```text
tasks
model_sources
base_models
trained_models
training_pipelines
training_jobs
datasets
dataset_samples
annotations
label_projects
devices
cameras
edge_apps
edge_app_versions
deployments
```

要求：

- `upgrade()` 使用 `op.create_table()` 创建全部表。
- `downgrade()` 按外键依赖反序删除全部表。
- 不创建 `users`、`roles`、`permissions`。
- SQLite 测试可执行，避免使用 SQLite 不支持的 PostgreSQL 专属类型。

## 任务 6：添加 YOLO26 基础模型种子数据

- [ ] **步骤 1：创建 `infra/seed/yolo26_base_models.json`**

写入 30 条对象，覆盖：

```text
tasks = detect, segment, semantic, pose, obb, classify
scales = n, s, m, l, x
```

每条记录包含：

```json
{
  "id": "yolo26-detect-n",
  "family": "yolo26",
  "task": "detect",
  "scale": "n",
  "filename": "yolo26n.pt",
  "source_path": "yolo26/detect/yolo26n.pt",
  "status": "remote_available"
}
```

文件命名规则：

- detect：`yolo26{scale}.pt`
- segment：`yolo26{scale}-seg.pt`
- semantic：`yolo26{scale}-sem.pt`
- pose：`yolo26{scale}-pose.pt`
- obb：`yolo26{scale}-obb.pt`
- classify：`yolo26{scale}-cls.pt`

## 任务 7：验证和提交

- [ ] **步骤 1：安装依赖**

运行：

```bash
python -m pip install -e ".[test,dev]"
```

- [ ] **步骤 2：运行迁移测试**

运行：

```bash
pytest tests/integration/test_migrations.py -v
```

预期：通过，至少 2 个测试通过。

- [ ] **步骤 3：运行已有测试**

运行：

```bash
pytest -v
```

预期：所有测试通过。

- [ ] **步骤 4：运行 lint**

运行：

```bash
ruff check apps packages tests
```

预期：通过。

- [ ] **步骤 5：提交**

运行：

```bash
git add pyproject.toml alembic.ini infra/migrations infra/seed packages/visiox-db tests/integration/test_migrations.py docs/superpowers/plans/2026-07-03-database-core.md
git commit -m "feat: add database core schema"
```

## 自查

- 规格覆盖：本计划覆盖 Task 2 要求的 SQLAlchemy model、Alembic migration 环境、30 条 YOLO26 基础模型 seed 和空库 migration upgrade 测试。
- 有意延后：不实现 repository helper、业务 API、seed runner、Task Center 状态流转逻辑和真实 PostgreSQL 容器测试；这些属于后续任务或 Docker 环境验证。
- 文档语言：本文档正文使用中文；代码、路径、命令、表名、字段名和技术标识保持英文。
