# Visiox

Visiox 是面向客户内网私有化部署的 YOLO26 视觉模型管理平台。MVP 覆盖数据准备、Label Studio 同步、训练产线、任务中心、模型导出、边缘应用打包、Edge Agent 部署和 Vue 管理台。

## 模块

- `apps/api-service`：FastAPI API、任务入口和 WebSocket 任务进度。
- `apps/frontend`：Vue 3 管理台。
- `apps/edge-agent`：边缘设备侧应用部署、启动、停止和回滚控制面。
- `apps/yolo26-inference`：统一 YOLO26 推理服务。
- `workers/*`：训练、模型下载、Label Studio 同步和部署 worker。
- `packages/*`：数据库、配置、消息、对象存储和 YOLO26 转换/训练/打包公共能力。
- `infra/compose`：本地 Docker Compose 栈。

## 快速验证

```powershell
.\.venv\Scripts\pytest -q tests\integration\test_mvp_yolo26_detect_flow.py tests\integration\test_mvp_labelstudio_flow.py
.\.venv\Scripts\ruff check apps packages tests infra workers
npm run test --prefix apps/frontend
npm run build --prefix apps/frontend
```

完整本地操作见 [docs/runbooks/local-mvp.md](docs/runbooks/local-mvp.md)。

## 启动

```powershell
docker compose -f infra/compose/docker-compose.yml up --build
```

前端开发服务：

```powershell
npm run dev --prefix apps/frontend -- --host 127.0.0.1 --port 5173
```

## 范围说明

MVP 是内部平台，不包含登录、用户、角色或权限体系。跨模块任务通过 Redis Stream/任务中心编排，持久状态以数据库为准。
