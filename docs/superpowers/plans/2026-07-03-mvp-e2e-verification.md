# Task 13：端到端 MVP 验证执行计划

> **给 agentic worker：** 本计划必须按步骤执行。所有新增文档使用中文；测试命名保持英文，测试说明和断言可使用英文。

**目标：** 为 Visiox MVP 增加可重复的端到端验证，覆盖 detect 数据集上传、训练任务、训练模型登记、边缘应用打包、部署到测试 Edge Agent，以及 Label Studio 项目创建和标注同步 smoke。

**架构：** 端到端测试沿用现有集成测试模式：SQLite 临时数据库、内存对象存储、假 Redis producer、假 Label Studio client、假训练/导出/Agent client。这样可以在没有 Docker daemon、GPU、真实 Label Studio 或真实 Redis 的开发机上稳定验证业务闭环。Compose 级验证放入 runbook，由开发者在具备 Docker 环境时执行。

---

## 任务 1：确认现有接口契约

**文件：**
- 读取：`apps/api-service/src/visiox_api/routes/*.py`
- 读取：`tests/integration/test_*`

步骤：
- [ ] 确认数据集上传路径为 `/datasets/{dataset_id}/samples:upload`。
- [ ] 确认数据集分析和校验路径为 `/datasets/{dataset_id}/analyze`、`/datasets/{dataset_id}/validate`。
- [ ] 确认训练 Job 路径为 `/pipelines/{pipeline_id}/jobs`。
- [ ] 确认边缘应用版本路径为 `/edge-apps/{edge_app_id}/versions`。
- [ ] 确认部署路径为 `/deployments`。

## 任务 2：新增 detect MVP 纵向测试

**文件：**
- 创建：`tests/integration/test_mvp_yolo26_detect_flow.py`

步骤：
- [ ] 创建临时 SQLite 数据库并执行 Alembic upgrade。
- [ ] 使用单一 `TestClient` 覆盖所有相关路由 session dependency。
- [ ] 使用 `InMemoryObjectStorageClient` 覆盖对象存储。
- [ ] 使用 fake stream producer 捕获训练、打包和部署命令。
- [ ] 通过 API 创建 detect 数据集并上传样本图片。
- [ ] 写入最小有效 detect annotation，并调用分析和校验 endpoint。
- [ ] 写入 ready 基础模型，创建 training pipeline 和 training job。
- [ ] 调用训练 worker，断言训练模型登记成功。
- [ ] 通过 API 创建 edge app 和 edge app version，再调用打包 worker。
- [ ] 注册设备并创建部署，再调用 deployment worker。
- [ ] 断言最终部署 running、任务 SUCCESS、对象存储包含训练模型和边缘包。

## 任务 3：新增 Label Studio smoke 测试

**文件：**
- 创建：`tests/integration/test_mvp_labelstudio_flow.py`

步骤：
- [ ] 创建 detect 数据集并上传样本。
- [ ] 使用 fake Label Studio client 创建项目。
- [ ] 通过 API 创建 Label Studio 项目并触发样本同步任务。
- [ ] 调用 label sync worker，把样本同步到 fake client。
- [ ] 设置 fake export payload 并调用 annotation import worker。
- [ ] 断言 annotation、dataset 计数、sample 状态和任务状态被正确更新。

## 任务 4：新增本地 MVP runbook

**文件：**
- 创建：`docs/runbooks/local-mvp.md`

步骤：
- [ ] 写清本地前置条件：Python、Node、Docker、端口和 `.env`。
- [ ] 写清初始化、测试、前端启动和 Compose 启动命令。
- [ ] 写清 MVP 手工验证流程。
- [ ] 写清当前已知限制：无登录/RBAC、训练和推理为本地/测试替身时不需要 GPU、真实 Compose 需要 Docker 环境。

## 任务 5：补 README 入口

**文件：**
- 创建或修改：`README.md`

步骤：
- [ ] 用中文介绍项目定位和模块。
- [ ] 链接本地 MVP runbook。
- [ ] 列出关键开发命令。

## 任务 6：验证与提交

步骤：
- [ ] 运行新增端到端测试。
- [ ] 运行相关集成测试。
- [ ] 运行 ruff。
- [ ] 若前端文件未变更，可不重跑前端 build；若 README/runbook 不影响前端，可记录不适用。
- [ ] 提交为 `test: add mvp end-to-end verification`。

## 验收标准

- `tests/integration/test_mvp_yolo26_detect_flow.py` 通过。
- `tests/integration/test_mvp_labelstudio_flow.py` 通过。
- `docs/runbooks/local-mvp.md` 能指导开发者在干净机器上启动和验证 MVP。
- `README.md` 提供项目入口和验证入口。
- 不引入用户、权限或登录体系。
