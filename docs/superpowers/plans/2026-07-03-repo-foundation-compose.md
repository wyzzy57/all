# Repository Foundation And Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable Visiox repository foundation with a minimal FastAPI health endpoint, Python packaging, Docker Compose infrastructure, and an integration test.

**Architecture:** Keep Task 1 intentionally thin: one API service package, one shared settings package, Compose definitions for infrastructure, and a health endpoint that reports configuration without requiring business tables. Do not introduce database models, Redis task contracts, storage clients, frontend code, or workers in this task.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, pytest, httpx, Docker Compose, PostgreSQL, Redis, MinIO, Registry, Label Studio.

---

## Files

- Create: `pyproject.toml` for workspace Python dependencies and pytest configuration.
- Create: `package.json` for repository-level convenience scripts.
- Create: `.env.example` for local Compose configuration.
- Create: `infra/compose/docker-compose.yml` for the minimal platform stack.
- Create: `infra/compose/docker-compose.dev.yml` for local source-mounted API development.
- Create: `apps/api-service/Dockerfile` for the API service image.
- Create: `apps/api-service/src/visiox_api/__init__.py`.
- Create: `apps/api-service/src/visiox_api/main.py` with `GET /health`.
- Create: `packages/visiox-common/src/visiox_common/__init__.py`.
- Create: `packages/visiox-common/src/visiox_common/settings.py`.
- Create: `tests/integration/test_api_health.py`.
- Modify: `.gitignore`.

## Task 1: Add Failing Health Test

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_api_health.py`:

```python
from fastapi.testclient import TestClient

from visiox_api.main import create_app


def test_health_returns_service_status_and_dependency_configuration(monkeypatch):
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/integration/test_api_health.py -v
```

Expected: FAIL because `visiox_api` is not importable yet.

## Task 2: Add Python Packaging And Settings

- [ ] **Step 1: Create Python package metadata**

Create `pyproject.toml`:

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

- [ ] **Step 2: Create package init files**

Create `apps/api-service/src/visiox_api/__init__.py`:

```python
"""Visiox API service."""
```

Create `packages/visiox-common/src/visiox_common/__init__.py`:

```python
"""Shared Visiox package utilities."""
```

- [ ] **Step 3: Create settings module**

Create `packages/visiox-common/src/visiox_common/settings.py`:

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

## Task 3: Implement Minimal API Health Endpoint

- [ ] **Step 1: Create FastAPI application**

Create `apps/api-service/src/visiox_api/main.py`:

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

- [ ] **Step 2: Run the health test to verify it passes**

Run:

```bash
pytest tests/integration/test_api_health.py -v
```

Expected: PASS.

## Task 4: Add Compose And Runtime Files

- [ ] **Step 1: Add environment example**

Create `.env.example`:

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

- [ ] **Step 2: Add API Dockerfile**

Create `apps/api-service/Dockerfile`:

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

- [ ] **Step 3: Add Compose stack**

Create `infra/compose/docker-compose.yml`:

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
    image: minio/minio:RELEASE.2026-06-13T11-33-47Z
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

- [ ] **Step 4: Add dev Compose override**

Create `infra/compose/docker-compose.dev.yml`:

```yaml
services:
  api-service:
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

- [ ] **Step 5: Add npm convenience scripts**

Create `package.json`:

```json
{
  "name": "visiox",
  "private": true,
  "version": "0.1.0",
  "scripts": {
    "test": "pytest",
    "test:health": "pytest tests/integration/test_api_health.py -v",
    "compose:up": "docker compose -f infra/compose/docker-compose.yml up --build",
    "compose:dev": "docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml up --build",
    "compose:down": "docker compose -f infra/compose/docker-compose.yml down"
  }
}
```

- [ ] **Step 6: Modify `.gitignore`**

Ensure `.gitignore` contains:

```gitignore
.superpowers/
.worktrees/
.venv/
__pycache__/
.pytest_cache/
*.egg-info/
.env
```

## Task 5: Verify And Commit

- [ ] **Step 1: Run focused test**

Run:

```bash
pytest tests/integration/test_api_health.py -v
```

Expected: PASS.

- [ ] **Step 2: Validate Compose configuration**

Run:

```bash
docker compose -f infra/compose/docker-compose.yml config
```

Expected: exit code 0 and rendered services include `api-service`, `postgres`, `redis`, `minio`, `registry`, and `label-studio`.

- [ ] **Step 3: Optionally run Compose build/start if Docker is available**

Run:

```bash
docker compose -f infra/compose/docker-compose.yml up --build
```

Expected: platform services start and `GET /health` returns HTTP 200. If Docker is unavailable or images cannot be pulled, record the exact blocker and do not claim Compose startup passed.

- [ ] **Step 4: Commit**

Run:

```bash
git add pyproject.toml package.json .env.example .gitignore infra apps packages tests docs/superpowers/plans/2026-07-03-repo-foundation-compose.md
git commit -m "chore: scaffold platform foundation"
```

Expected: commit succeeds on `task1-repo-foundation`.

## Self-Review

- Spec coverage: This plan implements Task 1 only: packaging, health endpoint, Compose services, Dockerfile, environment template, and health integration test.
- Intentional deferral: No SQLAlchemy models, Alembic, Redis messaging abstractions, MinIO SDK wrapper, frontend, workers, or business APIs are introduced in this task.
- Verification standard: Passing `pytest tests/integration/test_api_health.py -v` verifies application behavior. Passing `docker compose -f infra/compose/docker-compose.yml config` verifies Compose syntax. Full Compose startup is attempted only when Docker and network access are available.
