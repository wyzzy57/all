# 本地 MVP 运行手册

本文档用于在开发机上验证 Visiox MVP。默认场景是客户内网私有化部署：不包含登录、用户、角色或权限体系。

## 前置条件

- Python 3.12
- Node.js 20 或更新版本
- Docker Desktop 或兼容的 Docker Engine
- PowerShell
- 可用端口：`8000`、`5173`、`5432`、`6379`、`9000`、`9001`、`5000`、`8080`

如果 C 盘空间不足，建议把 npm cache 指到工作区或 D 盘：

```powershell
New-Item -ItemType Directory -Force .tmp\npm-cache,.tmp\npm-tmp | Out-Null
$env:npm_config_cache=(Resolve-Path .tmp\npm-cache).Path
$env:TEMP=(Resolve-Path .tmp\npm-tmp).Path
$env:TMP=$env:TEMP
```

## 初始化

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test,dev]"
npm ci --prefix apps/frontend
Copy-Item .env.example .env
```

## 本地快速验证

后端和 worker 的确定性集成测试不依赖真实 Redis、MinIO、Label Studio、GPU 或 Docker：

```powershell
.\.venv\Scripts\pytest -q tests\integration\test_mvp_yolo26_detect_flow.py tests\integration\test_mvp_labelstudio_flow.py
.\.venv\Scripts\ruff check apps packages tests infra workers
npm run test --prefix apps/frontend
npm run build --prefix apps/frontend
```

新增的 MVP 测试覆盖：

- 创建 detect 数据集并上传图片样本。
- 写入最小 detect 标注，执行数据集分析和训练前校验。
- 创建训练产线和训练 Job。
- 通过训练 worker 导出 YOLO26 数据集、执行假训练命令并登记训练模型。
- 创建边缘应用版本，通过打包 worker 生成边缘应用包。
- 注册测试 Edge Agent 设备，创建部署并通过部署 worker 调用 fake Agent。
- 创建 Label Studio 项目、同步样本、导入标注并落库。

## 启动 Compose 栈

```powershell
docker compose -f infra/compose/docker-compose.yml up --build
```

开发模式使用源码挂载：

```powershell
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml up --build
```

关闭：

```powershell
docker compose -f infra/compose/docker-compose.yml down
```

## 启动前端

```powershell
npm run dev --prefix apps/frontend -- --host 127.0.0.1 --port 5173
```

访问：

- 前端管理台：`http://127.0.0.1:5173/`
- API 健康检查：`http://127.0.0.1:8000/health`

如需让前端连 Compose 中的 API：

```powershell
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm run dev --prefix apps/frontend -- --host 127.0.0.1 --port 5173
```

## 手工 MVP 验证路径

1. 在“数据准备”创建 detect 数据集并上传图片或 zip。
2. 使用 Label Studio 创建标注项目并同步样本。
3. 标注完成后导入标注，确认数据集样本和标注计数正确。
4. 在“模型空间”确认基础模型已就绪；未就绪时先触发下载。
5. 在“训练产线”选择基础模型、数据集和训练参数，创建训练 Job。
6. 在“任务中心”观察任务状态、进度和错误信息。
7. 训练成功后创建边缘应用版本，等待打包任务完成。
8. 在“设备与摄像头”注册测试 Edge Agent 并测试摄像头连接。
9. 在“部署记录”创建部署，确认部署状态进入 running。

## 已知限制

- MVP 不包含登录、RBAC、租户或审计审批。
- 本地确定性测试使用 fake 训练、fake 导出、fake Agent 和内存对象存储；真实 GPU 训练需要额外配置 YOLO26 运行环境。
- Compose 级验证需要本机 Docker 可用，并且镜像拉取可访问。
- 当前前端 API client 为手写契约，后续可接入 OpenAPI 生成。
- 前端生产构建会提示 Element Plus 相关 bundle 较大，这是当前 MVP 可接受限制。
