# Codex 继续开发交接记录（2026-07-06）

本文档用于在另一台电脑的 Codex 中继续开发 Visiox。它不是 Codex 原始聊天记录的完整导出；原始对话属于 Codex 会话环境，不是项目文件。这里整理的是继续开发所需的关键上下文、历史决策、当前状态、提交记录和操作命令。

## 仓库与分支

- GitHub 仓库：`https://github.com/wyzzy57/all.git`
- 当前工作分支：`task1-repo-foundation`
- 当前最新提交：`c252253 fix: stabilize frontend api proxy`
- 本地工作区曾位于：`D:\biji\biji\visiox\.worktrees\task1-repo-foundation`
- 主要入口：
  - 前端：`apps/frontend`
  - API：`apps/api-service`
  - 通用包：`packages/*`
  - Worker：`workers/*`
  - Compose：`infra/compose/docker-compose.yml`
  - 本地运行手册：`docs/runbooks/local-mvp.md`
  - 总体架构：`docs/superpowers/specs/2026-07-03-visiox-architecture-design.md`
  - MVP 总计划：`docs/superpowers/plans/2026-07-03-visiox-mvp-implementation-plan.md`

## 固定产品约束

- 项目是客户内网私有化部署平台。
- 不做登录、用户、权限、租户、RBAC。
- 公司要求各功能 Docker 化，并通过 Redis 通信。
- 数据准备是一级模块，不应被隐藏在训练流程里。
- 文档和面向用户的 UI 文案优先使用中文。
- 标注系统集成 Label Studio。
- 模型方向围绕 YOLO26，数据集需要支持常用 YOLO 和 COCO 格式。

## 已完成主线

MVP 计划已经按 13 个任务推进并基本落地：

1. 仓库基础与 Docker Compose
2. 数据库核心与迁移
3. Redis Messaging 与 Task Center
4. 对象存储与基础模型自动准备
5. 数据准备模块
6. Label Studio 集成
7. YOLO26 Dataset Converter
8. 训练产线与 Training Worker
9. 统一 YOLO26 推理服务
10. 前端管理台
11. MVP 端到端验证

边缘应用、部署记录、设备与摄像头、Edge Agent、部署 Worker 和手动基础模型下载链路已从当前产品范围删除。

最近与数据准备相关的提交：

```text
c252253 fix: stabilize frontend api proxy
e007c36 feat: redesign data preparation label studio flow
0d20b8c feat: support coco dataset folder upload
d191095 feat: support yolo dataset folder upload
d50211e style: stack data preparation layout
5e8178e fix: create dataset during folder upload
a81b792 feat: support dataset folder upload
e1a3842 feat: add label studio entry in data preparation
9b504df fix: support frontend model lists
```

## 当前数据准备功能状态

前端页面：`apps/frontend/src/views/data-preparation/DataPreparationView.vue`

当前页面已改为：

- 顶部三张导入卡片：
  - 未标注数据导入
  - 已标注数据导入
  - 视频文件导入（目前是提示入口，未完成真实视频切帧管线）
- 中部导入配置：
  - 新建数据集
  - 追加样本
  - 数据集名称
  - 任务类型
  - 类别
  - 文件/文件夹选择
- 下方数据集卡片：
  - 点击数据集卡片会进入 Label Studio 流程。
  - 如果还没有 Label Studio 项目，会先调用 API 创建项目。
  - API 返回 `project_url` 后，前端打开 Label Studio 项目页面。
- `数据同步` 按钮会为当前筛选出的数据集创建或复用 Label Studio 项目，并提交样本同步任务。

后端 Label Studio 入口：`apps/api-service/src/visiox_api/routes/label_projects.py`

- `POST /datasets/{dataset_id}/label-projects`
- `GET /datasets/{dataset_id}/label-projects`
- `POST /label-projects/{project_id}/sync-samples`
- `POST /label-projects/{project_id}/import-annotations`

`LabelProjectResponse` 已增加：

```json
{
  "project_url": "http://127.0.0.1:8080/projects/{external_project_id}/data"
}
```

`project_url` 根据 `VISIOX_LABEL_STUDIO_URL` 和 `external_project_id` 生成。

## 数据集上传支持状态

后端上传入口：

- 单文件：`POST /datasets/{dataset_id}/samples:upload`
- 批量/文件夹：`POST /datasets/{dataset_id}/samples:upload-batch`

已支持：

- 图片：`.jpg`、`.jpeg`、`.png`、`.bmp`、`.webp`
- 压缩包：`.zip`
- YOLO 文件夹：
  - `data.yaml`
  - `images/`
  - `labels/`
- COCO 文件夹：
  - 常见 `annotations/*.json`
  - 自动读取类别和图片引用
  - 自动转换为平台内部样本/标注结构

用户之前提供的数据目录 `D:\huajiao\visio_huajiao` 被判断为接近 COCO 格式，因此后续增加了 COCO 自动识别与转换。

## 当前已知问题

### 1. Label Studio 未启动时不能跳转

最后一次本机检查时：

- `http://127.0.0.1:8000/health` 曾可用，后来本地 API 被停止。
- `http://127.0.0.1:5173/datasets` 在修复后可通过 Vite 代理访问 API。
- `http://127.0.0.1:8080` 连接被拒绝，说明 Label Studio 没有启动。
- 当前环境命令行里没有 `docker`，因此当时无法直接启动 Label Studio 容器。

如果在另一台电脑继续开发，需要先确认 Docker 可用，然后启动 Compose 栈。

### 2. 前端 `Failed to fetch` 已修复

原因：前端 dev server 原来没有 API 代理，如果启动时没带 `VITE_API_BASE_URL=http://127.0.0.1:8000`，浏览器会请求错误地址。

修复：`apps/frontend/vite.config.ts` 已增加开发代理，将 `/datasets`、`/label-projects`、`/tasks` 等 API 路径代理到 `http://127.0.0.1:8000`。

### 3. C 盘空间不足会影响 npm 和浏览器插件

本机曾出现 C 盘空间不足，导致：

- `npm run test` 写默认 npm cache 失败。
- Codex browser 插件初始化失败。

临时处理方式：

```powershell
New-Item -ItemType Directory -Force .tmp\npm-cache,.tmp\npm-tmp | Out-Null
$env:npm_config_cache=(Resolve-Path .tmp\npm-cache).Path
$env:TEMP=(Resolve-Path .tmp\npm-tmp).Path
$env:TMP=$env:TEMP
```

## 另一台电脑继续开发步骤

### 1. 拉取仓库

```powershell
git clone https://github.com/wyzzy57/all.git
cd all
git checkout task1-repo-foundation
```

如果仓库根目录不是 Visiox 项目根目录，需要根据实际 clone 内容进入包含 `apps`、`packages`、`infra`、`docs` 的目录。

### 2. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test,dev]"
npm ci --prefix apps/frontend
Copy-Item .env.example .env
```

### 3. 启动完整 Compose 栈

```powershell
docker compose -f infra/compose/docker-compose.yml up --build
```

服务端口：

- API：`http://127.0.0.1:8000`
- 前端：开发模式另启，默认 `http://127.0.0.1:5173`
- Label Studio：`http://127.0.0.1:8080`
- MinIO：`http://127.0.0.1:9000`
- MinIO Console：`http://127.0.0.1:9001`
- Registry：`http://127.0.0.1:5000`

Compose 默认 Label Studio 账号：

- 用户名：`admin@example.com`
- 密码：`visiox123`

### 4. 启动前端开发服务器

```powershell
npm run dev --prefix apps/frontend -- --host 127.0.0.1 --port 5173
```

前端已有 dev proxy，通常不再需要手工设置 `VITE_API_BASE_URL`。

### 5. 快速验证

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173/datasets
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080
```

如果 `/datasets` 能通过 `5173` 返回 JSON，说明前端代理正常。

## 常用测试命令

```powershell
.\.venv\Scripts\ruff.exe check apps packages tests infra workers
.\.venv\Scripts\pytest.exe -q tests\integration\test_dataset_upload.py tests\integration\test_label_studio_sync.py
npm run test --prefix apps/frontend
npm run build --prefix apps/frontend
```

最近一次相关验证结果：

- `ruff check apps packages tests infra workers` 通过
- `tests/integration/test_label_studio_sync.py` 20 passed
- 前端 vitest：4 passed
- 前端 build 通过
- build 仍有 Element Plus/VueUse 相关大包体积警告，当前 MVP 可接受

## 建议下一步

优先级从高到低：

1. 在 Docker 可用环境启动 Label Studio，验证点击数据集卡片是否能创建项目并跳转。
2. 完善视频文件导入：上传视频、配置切帧策略、生成图片样本、进入数据集。
3. 为数据准备页加更明确的导入进度和失败明细。
4. 增加前端端到端测试，覆盖：
   - 文件夹上传
   - COCO 导入
   - 点击数据集创建 Label Studio 项目
   - Label Studio 不可用时展示中文错误
5. 后续可把手写 API client 改成 OpenAPI 生成，减少前后端契约漂移。

## 给下一位 Codex 的提示

- 不要恢复或删除用户未明确要求删除的本地文件。
- 文档继续写中文。
- 不要引入登录、用户、权限体系。
- 数据准备是一级模块，继续围绕“上传数据集、去 Label Studio 标注、同步标注、转换为 YOLO26 训练格式”推进。
- 前端是 Vue 3 + Element Plus。
- 后端是 FastAPI，数据库模型在 `packages/visiox-db`，公共配置在 `packages/visiox-common`。
- 如果遇到 `Failed to fetch`，先检查：
  - API 是否监听 `8000`
  - 前端是否监听 `5173`
  - `apps/frontend/vite.config.ts` 的 proxy 是否生效
  - 浏览器实际请求是否打到 `/datasets`、`/label-projects`
- 如果遇到 Label Studio 跳转失败，先检查：
  - `http://127.0.0.1:8080` 是否可访问
  - `VISIOX_LABEL_STUDIO_URL` 是否是浏览器可访问地址
  - Compose 中 `label-studio` 服务是否启动
