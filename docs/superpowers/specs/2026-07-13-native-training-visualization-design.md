# VisioX 原生可视化训练设计

## 目标

将当前嵌入 MLflow 和 TensorBoard 的训练页面替换为 VisioX 原生仪表盘。MLflow 与 TensorBoard 只承担训练数据采集和存储，浏览器只访问 VisioX API，不直接依赖第三方页面或数据格式。

## 范围

第一版覆盖进入训练阶段的全部训练任务，包括运行中、成功、失败和中止状态。展示训练概览、标量曲线、资源曲线、Ultralytics 结果图、模型计算图、权重直方图和梯度直方图。MLflow 与 TensorBoard 原始界面保留为高级调试入口，不作为默认体验。

不在本阶段实现模型转换、TensorRT 构建和边缘下发；可视化 API 与模型版本标识必须保留后续连接边缘部署流程的能力。

## 架构

### 采集层

- Ultralytics 继续通过现有回调写入 MLflow 标量、参数和训练产物。
- TensorBoard 继续保存标量、计算图和直方图事件。
- VisioX 自定义回调记录权重和梯度直方图，默认在第 1 轮、每 5 轮和最后一轮采样。
- Worker 记录训练进度、当前 Epoch、总 Epoch、已用时间、预计剩余时间、CPU、内存、GPU 利用率和显存占用。
- 训练失败或被取消时不得删除已经生成的观测数据。

### 聚合层

API service 新增训练可视化聚合模块，通过稳定的 VisioX DTO 隔离第三方格式：

- MLflow 客户端读取 run 参数、标量历史、状态、时间和 artifact 元数据。
- TensorBoard event accumulator 读取计算图、直方图和缺少于 MLflow 的标量。
- 现有 TrainingJob、TrainingPipeline、Task 数据用于任务身份、产线名称和业务状态。
- 现有 MinIO 训练产物接口继续提供混淆矩阵、PR/F1/P/R 曲线和批次图。
- 单个数据源不可用时返回其余可用数据，并在 `availability` 中给出缺失原因，不把整个页面变成 500 错误。

### 展示层

`/training-visualization` 保留左侧真实训练记录列表，右侧替换为原生页签：

1. 概览：状态、进度、Epoch、耗时、剩余时间、环境和核心指标。
2. 指标曲线：Loss、Precision、Recall、mAP50、mAP50-95 和学习率，支持图例开关与悬浮读数。
3. 资源监控：CPU、内存、GPU、显存和训练吞吐。
4. 训练分析：混淆矩阵、PR/F1/P/R 曲线及 Ultralytics 结果图。
5. 模型结构：计算图节点与连接关系，可缩放、拖拽和查看层信息。
6. 参数分布：按层和 Epoch 选择权重或梯度直方图。

图表使用 ECharts。计算图使用 ECharts Graph，第一版避免引入完整图编辑器。页面不展示使用说明性文案，只提供必要状态、空态和错误信息。

## API 契约

- `GET /training-jobs/{id}/observability/summary`
  - 返回任务、产线、运行状态、进度、环境、时间、核心指标和数据可用性。
- `GET /training-jobs/{id}/observability/scalars`
  - 查询参数：`keys`、`start_step`、`end_step`。
  - 返回按指标分组的 `{step, value, timestamp}` 序列。
- `GET /training-jobs/{id}/observability/resources`
  - 返回 CPU、内存、GPU、显存和吞吐时间序列。
- `GET /training-jobs/{id}/observability/graph`
  - 返回稳定的 `{nodes, edges}` 结构和图可用性。
- `GET /training-jobs/{id}/observability/histograms`
  - 查询参数：`kind=weight|gradient`、`tag`、`step`。
  - 返回桶边界、桶计数、最值、均值和标准差。
- 训练结果图片继续通过现有 artifacts API 获取。

所有端点必须验证 TrainingJob 存在。第三方服务连接信息只存在后端配置中，不返回给浏览器。

## 数据兼容

- 新训练必须产生完整观测数据。
- 旧训练只展示当前已有的数据。
- 缺失数据使用明确空态，不生成模拟指标，不把最终评估分数扩展成虚假的逐 Epoch 曲线。
- 兼容旧状态值 `success` 与新状态值 `succeeded`。

## 性能与可靠性

- 标量序列按查询指标和步数范围读取，默认限制点数并在服务端下采样。
- 计算图与直方图按需加载，不包含在 summary 响应中。
- event 文件读取结果使用基于文件修改时间的内存缓存。
- 前端在训练中任务上定时刷新 summary 和最新标量；结束状态停止轮询。
- MLflow 或 TensorBoard 故障只影响对应面板，任务列表和已有训练产物仍可查看。

## 测试

- 单元测试覆盖 MLflow 指标归一化、TensorBoard 事件解析、直方图桶转换、下采样和缺失数据。
- API 集成测试覆盖完整数据、部分数据、任务不存在和数据源不可用。
- 前端组件测试覆盖任务选择、页签懒加载、曲线渲染输入、空态和轮询停止。
- 浏览器验证桌面布局、图表非空、页签切换和 MLflow/TensorBoard 不可用时的降级表现。

## 完成标准

- VisioX 默认页面不再包含 MLflow 或 TensorBoard iframe。
- 至少一个新训练任务能在原生页面显示真实 Epoch 曲线、结果图和权重直方图。
- 有计算图数据时可交互查看模型结构。
- 训练失败、中止或观测服务不可用时页面仍保持可用并显示真实状态。
