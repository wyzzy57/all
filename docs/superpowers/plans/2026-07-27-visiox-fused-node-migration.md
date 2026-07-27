# VisiOX Fused-Node Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the current VisiOX working tree and all active business data to `10.10.13.20` while preserving that server as the existing SSH/Docker GPU edge node.

**Architecture:** Build a static frontend container that proxies same-origin API and WebSocket requests, then layer a server-specific Compose override over the existing development Compose file. Take a cold logical/file snapshot from the Windows host, clean only disposable remote training objects, restore named volumes on the server, and reconcile the migrated control-plane records with the existing edge containers.

**Tech Stack:** Vue 3/Vite, Nginx, FastAPI, PostgreSQL 16, Redis 7, MinIO, Label Studio, MLflow, TensorBoard, Docker Compose v2, PowerShell, SSH/SCP.

---

### Task 1: Production frontend container

**Files:**
- Create: `apps/frontend/Dockerfile`
- Create: `apps/frontend/nginx.conf`
- Test: `apps/frontend/package.json`

- [ ] **Step 1: Build the frontend before containerizing it**

Run:

```powershell
npm --prefix apps/frontend run build
```

Expected: `vue-tsc` and `vite build` exit with code 0 and create `apps/frontend/dist`.

- [ ] **Step 2: Add a multi-stage frontend image**

Use Node 24 Alpine to run `npm ci` and `npm run build`, then copy `dist` into
`nginx:stable-alpine`. The runtime image exposes port 80 and has no Node process.

- [ ] **Step 3: Add SPA and API proxy routing**

Configure Nginx to:

```nginx
location / { try_files $uri $uri/ /index.html; }
location ~ ^/(base-models|datasets|health|label-projects|llm|nodes|pipelines|resource-pools|services|tasks|trained-models|training-jobs)(/|$) {
    proxy_pass http://api-service:8000;
}
location /ws { proxy_pass http://api-service:8000; proxy_http_version 1.1; }
```

Include forwarded headers, WebSocket upgrade headers, and upload body size `2g`.

- [ ] **Step 4: Verify the image locally**

Run:

```powershell
docker build -f apps/frontend/Dockerfile -t visiox-frontend:migration .
docker run --rm -d --name visiox-frontend-check -p 15174:80 visiox-frontend:migration
curl.exe -f http://127.0.0.1:15174/
docker stop visiox-frontend-check
```

Expected: the HTTP request returns the built VisiOX HTML.

### Task 2: Server Compose overlay

**Files:**
- Create: `infra/compose/docker-compose.server.yml`
- Modify: `.env.example`

- [ ] **Step 1: Add the server frontend service**

Add `frontend`, built from `apps/frontend/Dockerfile`, mapped as
`5174:80`, depending on `api-service`, and restarted with `unless-stopped`.

- [ ] **Step 2: Remove unsafe or conflicting host bindings**

Use Compose `!reset` in the overlay so PostgreSQL and Redis have no host ports,
and remap Label Studio Gateway from host port 80 to `8081:8081`. Keep API 8000,
MinIO 9000/9001, Registry 5000, MLflow 5001, and TensorBoard 6006 available on
the trusted LAN because current platform flows reference those endpoints.

- [ ] **Step 3: Add server public URL variables**

Document these values in `.env.example` without secrets:

```dotenv
VISIOX_PUBLIC_BASE_URL=http://10.10.13.20:5174
VISIOX_LABEL_STUDIO_PUBLIC_URL=http://10.10.13.20:8081
VISIOX_MLFLOW_PUBLIC_URL=http://10.10.13.20:5001
VISIOX_TENSORBOARD_PUBLIC_URL=http://10.10.13.20:6006
```

- [ ] **Step 4: Validate merged Compose configuration**

Run:

```powershell
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.server.yml config --quiet
```

Expected: exit code 0; merged configuration has no host binding on 80, 5432, or 6379.

### Task 3: Remote host preservation and capacity recovery

**Files:**
- Record: remote `/home/skyinfor/visiox-migration/<timestamp>/preflight/`

- [ ] **Step 1: Capture rollback evidence**

Over SSH, save `docker ps -a --no-trunc`, `docker images --digests`, `docker info`,
`df -h`, listening ports, and `/home/skyinfor/.visiox` disk usage.

- [ ] **Step 2: Remove only disposable stopped training containers**

Run a filtered removal for names matching `^visiox-train-` whose state is
`exited` or `created`. Do not use `docker container prune`.

- [ ] **Step 3: Prune unreferenced images and build cache**

Run `docker image prune -a -f` only after the inference containers remain in
`docker ps -a`, then run `docker builder prune -a -f`. Docker retains images
referenced by the preserved containers.

- [ ] **Step 4: Install Docker Compose v2**

Prefer Ubuntu package `docker-compose-v2`; if unavailable, install the official
Docker Compose CLI plugin. Verify with `sudo docker compose version`.

- [ ] **Step 5: Recheck capacity and ports**

Require at least 25GB free before uploading the snapshot and ensure ports 5000,
5001, 5174, 6006, 8000, 8081, 9000, and 9001 do not collide with preserved
inference services.

### Task 4: Cold snapshot and transfer

**Files:**
- Create locally: `.migration/<timestamp>/postgres.sql.gz`
- Create locally: `.migration/<timestamp>/{minio,label-studio,training-runs,mlflow,agent-pki}.tar.gz`
- Create locally: `.migration/<timestamp>/visiox-source.tar.gz`
- Transfer to: `/home/skyinfor/visiox-migration/<timestamp>/incoming/`

- [ ] **Step 1: Record migration counts**

Query API list totals for pipelines, datasets, training jobs, trained models,
services, and label projects before stopping writers. Save the JSON responses.

- [ ] **Step 2: Stop local writers**

Stop `api-service`, `edge-executor-worker`, `label-sync-worker`, and
`training-worker`, then stop Label Studio and MLflow before archiving their
volumes. Keep PostgreSQL and storage containers available for export.

- [ ] **Step 3: Export PostgreSQL**

Run `pg_dump --clean --if-exists --no-owner --no-privileges` inside
`compose-postgres-1`, stream the output to the host, and gzip it.

- [ ] **Step 4: Archive persistent volumes**

Use short-lived Alpine containers with the named volume mounted read-only and
the migration directory mounted writable. Archive MinIO, Label Studio,
training-runs, MLflow, and agent-pki separately so each restore is retryable.

- [ ] **Step 5: Package the current working tree**

Create a source archive from tracked and unignored working-tree files. Exclude
`.git`, `.env`, `.local-secrets`, `node_modules`, `.venv`, `dist`, `.migration`,
and generated caches. Include all current uncommitted LLM implementation files.

- [ ] **Step 6: Resume the local platform**

Restart the stopped local Compose services immediately after all snapshots have
completed, before the network transfer begins.

- [ ] **Step 7: Transfer and verify checksums**

Copy source, data archives, PostgreSQL dump, and the 32-byte edge credential
master key by SCP. Compare SHA-256 checksums on both hosts before restore.

### Task 5: Remote restore and startup

**Files:**
- Create remotely: `/home/skyinfor/visiox/.env`
- Create remotely: `/home/skyinfor/visiox/.local-secrets/edge_credential_master_key`

- [ ] **Step 1: Extract source and create secrets**

Set the key file mode to `0600`. Generate fresh infrastructure passwords for
PostgreSQL and MinIO only if they match the credentials encoded in the migrated
environment; otherwise retain the source values so restored clients continue
to connect.

- [ ] **Step 2: Create named volumes and restore file data**

Create the Compose volumes using project name `visiox`, then extract each
archive into its corresponding volume. Do not restore edge-runtime or registry
volumes.

- [ ] **Step 3: Start PostgreSQL and restore the logical dump**

Start PostgreSQL, wait for `pg_isready`, then pipe the decompressed dump through
`psql` as the configured application user.

- [ ] **Step 4: Build and start platform services**

Run:

```bash
sudo docker compose -p visiox \
  -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.server.yml \
  up -d --build
```

Run `alembic upgrade head` once after the API image is available, then restart
API and workers so current LLM schema migrations are active.

- [ ] **Step 5: Verify container health and logs**

Require every long-running Compose container to stay up for at least 60 seconds.
Inspect API, edge worker, label worker, training worker, PostgreSQL, MinIO, and
frontend logs for tracebacks, restart loops, migration errors, or credential
decryption failures.

### Task 6: Fused-node reconciliation and acceptance

**Files:**
- Record: remote `/home/skyinfor/visiox-migration/<timestamp>/acceptance/`

- [ ] **Step 1: Verify migrated object counts**

Call the deployed APIs and compare totals with Task 4. Any mismatch blocks
cutover and is recorded before repair.

- [ ] **Step 2: Verify control plane to local edge SSH**

From the edge executor container, resolve and connect to `10.10.13.20:22` using
the migrated node credential. Run the existing node probe and confirm NVIDIA
GPU inventory is returned.

- [ ] **Step 3: Reconcile existing inference services**

Match migrated service records by container ID/name and endpoint. Start only
containers belonging to records that were running before migration, then run
the service health check and one image inference request. Never create a second
container when an existing revision is recoverable.

- [ ] **Step 4: Verify platform integrations**

Open VisiOX, Label Studio gateway, MLflow, and TensorBoard from the LAN. Verify
the frontend SPA survives a deep-link refresh and API requests remain
same-origin through the frontend proxy.

- [ ] **Step 5: Run a minimal GPU training smoke test**

Submit a two-epoch detection job to the fused node, verify remote Docker/NVIDIA
execution, scalar/resource collection, TensorBoard output, trained-model record,
and downloadable `best.pt`.

- [ ] **Step 6: Preserve rollback state**

Keep the local platform and migration archives until the user explicitly
accepts the server deployment. Record final container IDs, image digests,
checksums, ports, object counts, and smoke-test IDs.
