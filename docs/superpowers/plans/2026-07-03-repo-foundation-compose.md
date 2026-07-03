# 仓库基础与 Compose 实施计划

> **给 agentic worker：** 必须使用子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 构建第一版可运行的 Visiox 仓库基础，包含最小 FastAPI health endpoint、Python packaging、Docker Compose 基础设施和集成测试。

**架构：** 任务 1 刻意保持轻量：一个 API service package、一个共享 settings package、基础设施 Compose 定义，以及一个只报告配置且不依赖业务表的 health endpoint。本任务不引入数据库 model、Redis task contract、storage client、前端代码或 worker。

**技术栈：** Python 3.12、FastAPI、Pydantic Settings、pytest、httpx、ruff、Docker Compose、PostgreSQL、Redis、MinIO、Registry、Label Studio。

---

## 文件

- 创建：用于 workspace Python dependency、pytest 配置和 ruff dependency 的 `pyproject.toml`。
- 创建：包含仓库级便捷脚本的 `package.json`。
- 创建：本地 Compose 配置的 `.env.example`。
- 创建：最小平台栈 `infra/compose/docker-compose.yml`。
- 创建：本地源码挂载式 API 开发配置 `infra/compose/docker-compose.dev.yml`。
- 创建：API service 镜像文件 `apps/api-service/Dockerfile`。
- 创建：`apps/api-service/src/visiox_api/__init__.py`。
- 创建：包含 `GET /health` 的 `apps/api-service/src/visiox_api/main.py`。
- 创建：`packages/visiox-common/src/visiox_common/__init__.py`。
- 创建：`packages/visiox-common/src/visiox_common/settings.py`。
- 创建：`tests/integration/test_api_health.py`。
- 修改：`.gitignore`。

## 任务 1：添加失败的 Health 测试

- [ ] **步骤 1：编写失败的集成测试**

创建 `tests/integration/test_api_health.py`。测试必须在设置环境变量前调用 `get_settings.cache_clear()`，并在 `finally` 中再次调用 `get_settings.cache_clear()`，避免 Pydantic settings cache 污染其他测试：

```python
from fastapi.testclient import TestClient

from visiox_common.settings import get_settings
from visiox_api.main import create_app


def test_health_returns_service_status_and_dependency_configuration(monkeypatch):
    get_settings.cache_clear()
    try:
        monkeypatch.setenv("VISIOX_ENV", "test")
        monkeypatch.setenv("VISIOX_POSTGRES_DSN", "postgresql+psycopg://visiox:visiox@postgres:5432/visiox")
        monkeypatch.setenv("VISIOX_REDIS_URL", "redis://redis:6379/0")
        monkeypatch.setenv("VISIOX_MINIO_ENDPOINT", "minio:9000")
        monkeypatch.setenv("VISIOX_REGISTRY_URL", "registry:5000")
        monkeypatch.setenv("VISIOX_LABEL_STUDIO_URL", "http://label-studio:8080")

        client = TestClient(create_app())

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "service": "api-service",
            "status": "ok",
            "environment": "test",
            "dependencies": {
                "postgres": "postgresql+psycopg://visiox:visiox@postgres:5432/visiox",
                "redis": "redis://redis:6379/0",
                "minio": "minio:9000",
                "registry": "registry:5000",
                "label_studio": "http://label-studio:8080",
            },
        }
    finally:
        get_settings.cache_clear()
```

- [ ] **步骤 2：运行测试并确认失败**

运行：

```bash
pytest tests/integration/test_api_health.py -v
```

预期：FAIL，因为 `visiox_api` 尚不可导入。

## 任务 2：添加 Python Packaging 与 Settings

- [ ] **步骤 1：创建 Python package metadata**

创建 `pyproject.toml`：

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "visiox"
version = "0.1.0"
description = "Private YOLO26 model management platform"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115,<1.0",
  "httpx>=0.27,<1.0",
  "pydantic-settings>=2.4,<3.0",
  "psycopg[binary]>=3.2,<4.0",
  "uvicorn[standard]>=0.30,<1.0",
]

[project.optional-dependencies]
test = [
  "pytest>=8.3,<9.0",
]
dev = [
  "ruff>=0.8,<1.0",
]

[tool.hatch.build.targets.wheel]
packages = [
  "apps/api-service/src/visiox_api",
  "packages/visiox-common/src/visiox_common",
]

[tool.pytest.ini_options]
pythonpath = [
  "apps/api-service/src",
  "packages/visiox-common/src",
]
testpaths = ["tests"]
```

- [ ] **步骤 2：创建 package init 文件**

创建 `apps/api-service/src/visiox_api/__init__.py`：

```python
"""Visiox API service."""
```

创建 `packages/visiox-common/src/visiox_common/__init__.py`：

```python
"""Shared Visiox package utilities."""
```

- [ ] **步骤 3：创建 settings module**

创建 `packages/visiox-common/src/visiox_common/settings.py`：

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISIOX_", env_file=".env", extra="ignore")

    environment: str = "local"
    postgres_dsn: str = "postgresql+psycopg://visiox:visiox@postgres:5432/visiox"
    redis_url: str = "redis://redis:6379/0"
    minio_endpoint: str = "minio:9000"
    registry_url: str = "registry:5000"
    label_studio_url: str = "http://label-studio:8080"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

## 任务 3：实现最小 API Health Endpoint

- [ ] **步骤 1：创建 FastAPI application**

创建 `apps/api-service/src/visiox_api/main.py`：

```python
from fastapi import FastAPI

from visiox_common.settings import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title="Visiox API", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, object]:
        settings = get_settings()
        return {
            "service": "api-service",
            "status": "ok",
            "environment": settings.environment,
            "dependencies": {
                "postgres": settings.postgres_dsn,
                "redis": settings.redis_url,
                "minio": settings.minio_endpoint,
                "registry": settings.registry_url,
                "label_studio": settings.label_studio_url,
            },
        }

    return app


app = create_app()
```

- [ ] **步骤 2：运行 health 测试并确认通过**

运行：

```bash
pytest tests/integration/test_api_health.py -v
```

预期：PASS。

## 任务 4：添加 Compose 和 Runtime 文件

- [ ] **步骤 1：添加环境变量示例**

创建 `.env.example`：

```dotenv
VISIOX_ENV=local
VISIOX_POSTGRES_DSN=postgresql+psycopg://visiox:visiox@postgres:5432/visiox
VISIOX_REDIS_URL=redis://redis:6379/0
VISIOX_MINIO_ENDPOINT=minio:9000
VISIOX_REGISTRY_URL=registry:5000
VISIOX_LABEL_STUDIO_URL=http://label-studio:8080

POSTGRES_DB=visiox
POSTGRES_USER=visiox
POSTGRES_PASSWORD=visiox

MINIO_ROOT_USER=visiox
MINIO_ROOT_PASSWORD=visiox123

LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK=true
LABEL_STUDIO_USERNAME=admin@example.com
LABEL_STUDIO_PASSWORD=visiox123
```

- [ ] **步骤 2：添加 API Dockerfile**

创建 `apps/api-service/Dockerfile`：

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY apps/api-service ./apps/api-service
COPY packages/visiox-common ./packages/visiox-common

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "visiox_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **步骤 3：添加 Compose stack**

创建 `infra/compose/docker-compose.yml`。`minio` 镜像必须固定为 `minio/minio:RELEASE.2025-09-07T16-13-09Z`：

```yaml
services:
  api-service:
    build:
      context: ../..
      dockerfile: apps/api-service/Dockerfile
    env_file:
      - ../../.env.example
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_started
      redis:
        condition: service_started
      minio:
        condition: service_started
      registry:
        condition: service_started
      label-studio:
        condition: service_started

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-visiox}
      POSTGRES_USER: ${POSTGRES_USER:-visiox}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-visiox}
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  minio:
    image: minio/minio:RELEASE.2025-09-07T16-13-09Z
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:-visiox}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-visiox123}
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio-data:/data

  registry:
    image: registry:2
    ports:
      - "5000:5000"
    volumes:
      - registry-data:/var/lib/registry

  label-studio:
    image: heartexlabs/label-studio:latest
    environment:
      LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK: ${LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK:-true}
      LABEL_STUDIO_USERNAME: ${LABEL_STUDIO_USERNAME:-admin@example.com}
      LABEL_STUDIO_PASSWORD: ${LABEL_STUDIO_PASSWORD:-visiox123}
    ports:
      - "8080:8080"
    volumes:
      - label-studio-data:/label-studio/data

volumes:
  postgres-data:
  minio-data:
  registry-data:
  label-studio-data:
```

- [ ] **步骤 4：添加 dev Compose override**

创建 `infra/compose/docker-compose.dev.yml`。dev Compose 必须显式设置 `PYTHONPATH`，让源码挂载后可导入本地 package：

```yaml
services:
  api-service:
    environment:
      PYTHONPATH: /app/apps/api-service/src:/app/packages/visiox-common/src
    volumes:
      - ../../apps/api-service/src:/app/apps/api-service/src
      - ../../packages/visiox-common/src:/app/packages/visiox-common/src
    command:
      - uvicorn
      - visiox_api.main:app
      - --host
      - 0.0.0.0
      - --port
      - "8000"
      - --reload
```

- [ ] **步骤 5：添加 npm 便捷脚本**

创建 `package.json`，其中必须包含 `lint` 脚本：

```json
{
  "name": "visiox",
  "private": true,
  "version": "0.1.0",
  "scripts": {
    "lint": "ruff check apps packages tests",
    "test": "pytest",
    "test:health": "pytest tests/integration/test_api_health.py -v",
    "compose:up": "docker compose -f infra/compose/docker-compose.yml up --build",
    "compose:dev": "docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml up --build",
    "compose:down": "docker compose -f infra/compose/docker-compose.yml down"
  }
}
```

- [ ] **步骤 6：修改 `.gitignore`**

确保 `.gitignore` 包含：

```gitignore
.superpowers/
.worktrees/
.venv/
__pycache__/
.pytest_cache/
*.egg-info/
.env
```

## 任务 5：验证并提交

- [ ] **步骤 1：运行 focused test**

运行：

```bash
pytest tests/integration/test_api_health.py -v
```

预期：PASS。

- [ ] **步骤 2：运行 lint**

运行：

```bash
ruff check apps packages tests
```

预期：exit code 0。

- [ ] **步骤 3：校验 Compose 配置**

运行：

```bash
docker compose -f infra/compose/docker-compose.yml config
```

预期：exit code 0，渲染后的服务包含 `api-service`、`postgres`、`redis`、`minio`、`registry` 和 `label-studio`。

- [ ] **步骤 4：如 Docker 可用，可选运行 Compose build/start**

运行：

```bash
docker compose -f infra/compose/docker-compose.yml up --build
```

预期：平台服务启动，`GET /health` 返回 HTTP 200。如果 Docker 不可用或镜像无法拉取，记录准确阻塞原因，不要声称 Compose startup 已通过。

- [ ] **步骤 5：提交**

运行：

```bash
git add pyproject.toml package.json .env.example .gitignore infra apps packages tests docs/superpowers/plans/2026-07-03-repo-foundation-compose.md
git commit -m "chore: scaffold platform foundation"
```

预期：在 `task1-repo-foundation` 上提交成功。

## 自查

- 规格覆盖：本计划只实现任务 1：packaging、health endpoint、Compose 服务、Dockerfile、environment template、lint 脚本和 health 集成测试。
- 有意延后：不引入 SQLAlchemy model、Alembic、Redis messaging abstraction、MinIO SDK wrapper、前端、worker 或业务 API。
- 验证标准：`pytest tests/integration/test_api_health.py -v` 通过验证应用行为。`ruff check apps packages tests` 通过验证代码风格。`docker compose -f infra/compose/docker-compose.yml config` 通过验证 Compose 语法。仅在 Docker 和网络访问可用时尝试完整 Compose startup。
