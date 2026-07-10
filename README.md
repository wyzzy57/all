# Visiox

Visiox is a private YOLO26 model management platform for local deployment.

## Modules

- `apps/api-service`: FastAPI API, task entry points, and task-progress WebSocket.
- `apps/frontend`: Vue 3 management console.
- `apps/yolo26-inference`: Unified YOLO26 inference service.
- `workers/*`: training, model download, and Label Studio sync workers.
- `packages/*`: shared database, settings, messaging, object storage, and YOLO26 dataset/training utilities.
- `infra/compose`: local Docker Compose stack.

## Quick Verification

```powershell
.\.venv\Scripts\pytest -q
.\.venv\Scripts\ruff check apps packages tests infra workers
npm run test --prefix apps/frontend
npm run build --prefix apps/frontend
```

See [docs/runbooks/local-mvp.md](docs/runbooks/local-mvp.md) for the local MVP runbook.

## Startup

```powershell
docker compose -f infra/compose/docker-compose.yml up --build
```

Frontend dev server:

```powershell
npm run dev --prefix apps/frontend -- --host 127.0.0.1 --port 5173
```

## Scope

The current MVP keeps dataset preparation, Label Studio synchronization, training pipelines, task center, model space, and YOLO26 inference. Deployment records, edge applications, devices, cameras, Edge Agent, and deployment workers have been removed from the product scope.
