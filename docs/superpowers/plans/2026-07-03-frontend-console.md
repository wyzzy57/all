# 前端管理台执行计划

**任务编号：** Task 12

**目标：** 构建 Visiox MVP 前端管理台，让内部用户可以通过浏览器完成模型空间、数据准备、训练产线、任务中心、设备、边缘应用和部署的主要操作。

**架构：** 使用 Vue 3 + TypeScript + Vite + Element Plus。前端作为独立 app 放在 `apps/frontend`，通过可配置 API base URL 调用 FastAPI。第一版不实现登录页、权限体系或多租户 UI。

**技术栈：** Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router、Vitest、Vue Test Utils。

## 范围

- 初始化 `apps/frontend`。
- 实现 API client。
- 实现基础布局和导航。
- 实现页面：
  - 模型空间
  - 数据准备
  - 训练产线
  - 任务中心
  - 设备与摄像头
  - 边缘应用与部署
- 实现任务中心状态 store。
- 增加组件和页面测试。

## 非目标

- 不实现登录、角色、权限。
- 不生成 OpenAPI 类型代码。
- 不实现复杂图表。
- 不实现真实文件大上传的全部交互细节。
- 不接入 WebSocket 实时流；第一版使用 store 和手动刷新，保留后续接入点。

## 页面设计原则

- 管理台是内部生产工具，界面保持克制、密集、易扫描。
- 第一屏直接进入工作台，不做营销式首页。
- 使用表格、表单、步骤条、状态标签和抽屉/对话框承载工作流。
- 不做权限相关入口。

## 文件变更

### 1. 工程配置

- `apps/frontend/package.json`
- `apps/frontend/index.html`
- `apps/frontend/tsconfig.json`
- `apps/frontend/tsconfig.node.json`
- `apps/frontend/vite.config.ts`
- `apps/frontend/vitest.config.ts`

### 2. 应用入口

- `apps/frontend/src/main.ts`
- `apps/frontend/src/App.vue`
- `apps/frontend/src/router/index.ts`
- `apps/frontend/src/styles.css`

### 3. API 和 Store

- `apps/frontend/src/api/client.ts`
- `apps/frontend/src/stores/taskCenter.ts`

### 4. 页面

- `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- `apps/frontend/src/views/data-preparation/DataPreparationView.vue`
- `apps/frontend/src/views/pipelines/PipelinesView.vue`
- `apps/frontend/src/views/tasks/TasksView.vue`
- `apps/frontend/src/views/devices/DevicesView.vue`
- `apps/frontend/src/views/edge-apps/EdgeAppsView.vue`

### 5. 测试

- `apps/frontend/tests/*.spec.ts`

## 实施步骤

1. 创建本计划并提交。
2. 初始化 Vue/Vite/Element Plus 工程。
3. 实现 API client 和 task store。
4. 实现布局、导航和页面路由。
5. 实现各业务页面的表格、表单和操作入口。
6. 增加 Vitest 测试。
7. 运行前端测试、构建和全仓库检查。
8. 修复评审问题。
9. 提交为 `feat: add frontend console`。

## 验收标准

- `npm --prefix apps/frontend test` 通过。
- `npm --prefix apps/frontend run build` 通过。
- 页面不存在登录/权限相关入口。
- 用户可以从 UI 访问 MVP 主要资源和任务状态。
- 前端 API base URL 可通过 `VITE_API_BASE_URL` 配置。
