<template>
  <section class="model-space-view" :class="{ 'list-mode': viewMode === 'list' }">
    <template v-if="viewMode === 'list'">
      <header class="page-header">
        <h1>模型空间</h1>
        <div class="header-actions">
          <el-button v-if="batchMode" type="danger" plain @click="deleteSelectedPipelines">批量删除</el-button>
          <el-button plain @click="batchMode = !batchMode">{{ batchMode ? "取消批量" : "批量操作" }}</el-button>
          <el-button type="primary" data-testid="create-pipeline" @click="openCreateDialog">创建产线</el-button>
        </div>
      </header>

      <el-alert
        v-if="errorMessage"
        class="page-alert"
        type="warning"
        :title="errorMessage"
        show-icon
        :closable="false"
      />

      <div class="list-toolbar">
        <nav class="tabs" aria-label="产线筛选">
          <button
            v-for="tab in tabOptions"
            :key="tab.value"
            class="tab-button"
            :class="{ active: activeTab === tab.value }"
            type="button"
            @click="setTab(tab.value)"
          >
            {{ tab.label }}
          </button>
        </nav>

        <div class="toolbar-controls">
          <el-select v-model="sortMode" class="toolbar-select" placeholder="排序">
            <el-option label="时间倒序" value="newest" />
            <el-option label="时间正序" value="oldest" />
            <el-option label="名称排序" value="name" />
          </el-select>
          <el-select v-model="typeFilter" class="toolbar-select" placeholder="全部类型">
            <el-option label="全部类型" value="" />
            <el-option v-for="task in typeOptions" :key="task" :label="taskLabel(task)" :value="task" />
          </el-select>
          <el-input v-model="keyword" class="search-input" clearable placeholder="搜索" @input="currentPage = 1">
            <template #suffix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>
      </div>

      <div v-if="loading" class="pipeline-grid" aria-busy="true">
        <article v-for="index in pageSize" :key="index" class="pipeline-card skeleton-card">
          <div class="skeleton-title" />
          <div class="skeleton-line" />
          <div class="skeleton-pill" />
        </article>
      </div>

      <el-empty v-else-if="pagedPipelines.length === 0" description="暂无产线" />

      <div v-else class="pipeline-grid">
        <article
          v-for="pipeline in pagedPipelines"
          :key="pipeline.id"
          class="pipeline-card"
          :class="{ selected: selectedPipelineIds.has(pipeline.id) }"
          role="button"
          tabindex="0"
          :data-testid="`pipeline-card-${pipeline.id}`"
          @click="openPipelineCard(pipeline)"
          @keydown.enter.prevent="openPipelineCard(pipeline)"
        >
          <div class="card-actions" @click.stop>
            <el-checkbox
              v-if="batchMode"
              :model-value="selectedPipelineIds.has(pipeline.id)"
              @change="toggleSelectedPipeline(pipeline.id)"
            />
            <button class="icon-button" type="button" :aria-label="pipeline.is_favorite ? '取消收藏' : '收藏'" @click.stop="toggleFavorite(pipeline)">
              <el-icon><StarFilled v-if="pipeline.is_favorite" /><Star v-else /></el-icon>
            </button>
            <div class="more-wrap">
              <button class="icon-button more-button" type="button" aria-label="更多" @click.stop>
                <el-icon><MoreFilled /></el-icon>
              </button>
              <div class="more-menu">
                <button type="button" @click.stop="openRenameDialog(pipeline)">修改名称</button>
                <button type="button" @click.stop="openPublicDialog(pipeline)">公开配置</button>
                <button type="button" class="danger-text" @click.stop="deletePipeline(pipeline)">删除</button>
              </div>
            </div>
          </div>

          <div class="card-main">
            <h2 :title="pipeline.name">{{ pipeline.name }}</h2>
            <time>{{ formatTime(pipeline.created_at || pipeline.updated_at) }}</time>
            <span class="type-pill">{{ taskLabel(pipeline.task) }}</span>
          </div>
          <footer class="card-footer">
            <div class="status-action-wrap" @click.stop>
              <span class="status-pill" :class="statusClass(pipeline.status)">
                <el-icon v-if="statusClass(pipeline.status) === 'success'"><Check /></el-icon>
                <el-icon v-else-if="statusClass(pipeline.status) === 'danger'"><CircleClose /></el-icon>
                <el-icon v-else-if="statusClass(pipeline.status) === 'running'"><Loading /></el-icon>
                <el-icon v-else><Loading /></el-icon>
                {{ statusLabel(pipeline.status) }}
              </span>
              <button
                v-if="isTrainingPipeline(pipeline)"
                class="stop-training-button"
                type="button"
                :disabled="stoppingPipelineId === pipeline.id"
                @click.stop="stopTrainingPipeline(pipeline)"
              >
                停止训练
              </button>
            </div>
            <span class="owner-pill">我</span>
          </footer>
        </article>
      </div>

      <footer class="pagination-row" v-if="filteredPipelines.length > 0">
        <span class="total-count">共 {{ filteredPipelines.length }} 条</span>
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          background
          layout="prev, pager, next, sizes"
          :page-sizes="[20, 40, 60]"
          :total="filteredPipelines.length"
        />
      </footer>
    </template>

    <template v-else-if="viewMode === 'detail'">
      <section v-if="detailPipeline" class="pipeline-detail-view">
        <button class="back-link detail-back" type="button" @click="backToList">
          <el-icon><ArrowLeft /></el-icon>
          返回产线列表
        </button>

        <header class="pipeline-detail-header">
          <div>
            <h1>{{ detailPipeline.name }}</h1>
            <p>
              <span>Pipeline ID：{{ detailPipeline.id }}</span>
              <i></i>
              <span>Runtime:{{ detailRuntime }}</span>
            </p>
          </div>
          <div class="detail-actions">
            <el-button
              v-if="detailPipeline.framework_locked_at || detailPipeline.first_submitted_job_id"
              plain
              data-testid="clone-locked-pipeline"
              @click="clonePipelineForEditing(detailPipeline)"
            >克隆并更换框架</el-button>
            <el-button plain @click="deletePipeline(detailPipeline)">删除</el-button>
            <el-button plain @click="openPublicDialog(detailPipeline)">公开配置</el-button>
            <el-button plain @click="toggleFavorite(detailPipeline)">{{ detailPipeline.is_favorite ? "取消收藏" : "收藏" }}</el-button>
          </div>
        </header>

        <nav class="detail-tabs">
          <button :class="{ active: detailTab === 'basic' }" type="button" @click="detailTab = 'basic'">基础信息</button>
          <button :class="{ active: detailTab === 'logs' }" type="button" @click="openDetailTab('logs')">日志详情</button>
          <button type="button" @click="openTrainingVisualization">可视化训练</button>
          <button :class="{ active: detailTab === 'experience' }" type="button" @click="detailTab = 'experience'">在线体验</button>
          <button
            :class="{ active: detailTab === 'deploy' }"
            type="button"
            data-testid="detail-tab-deploy"
            @click="openDetailTab('deploy')"
          >部署</button>
          <button
            :class="{ active: detailTab === 'evaluate' }"
            type="button"
            data-testid="detail-tab-evaluate"
            @click="openDetailTab('evaluate')"
          >
            评估
          </button>
        </nav>

        <section v-if="detailTab === 'basic'" class="detail-panel basic-detail-panel">
          <div class="info-lines">
            <p><span>产线模板：</span><strong>{{ taskLabel(detailPipeline.task) }}</strong></p>
            <p><span>微调模型：</span><strong>{{ detailModelName }}</strong></p>
            <div class="param-line">
              <span>训练参数配置：</span>
              <table class="param-table">
                <tbody>
                  <tr v-for="row in detailParamRows" :key="row.label">
                    <th>{{ row.label }}</th>
                    <td>{{ row.value }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p><span>数据集：</span><strong>{{ detailDatasetName }}</strong></p>
            <p class="output-path-line">
              <span>输出路径：</span>
              <button class="table-link result-files-trigger" type="button" data-testid="open-result-files" @click="openResultFiles">
                结果文件
              </button>
            </p>
            <p><span>任务状态：</span><strong>{{ statusLabel(detailPipeline.status) }}</strong></p>
          </div>
        </section>

        <section v-else-if="detailTab === 'logs'" class="detail-panel logs-detail-panel">
          <div class="logs-toolbar">
            <button type="button" @click="loadDetailLog"><el-icon><Refresh /></el-icon>手动刷新</button>
            <button type="button" @click="downloadDetailLog"><el-icon><Download /></el-icon>下载日志</button>
          </div>
          <pre class="training-log-panel">{{ detailLogLoading ? "正在加载日志..." : detailLogText }}</pre>
        </section>

        <section v-else-if="detailTab === 'experience'" class="detail-panel experience-detail-panel">
          <ServiceExperiencePanel
            :service-name="detailPipeline.name"
            :show-inference-controls="true"
            :model-options="detailInferenceModelOptions"
            :environment-options="inferenceEnvironmentOptions"
            default-environment="cpu"
            :run-inference="runDetailInference"
          />
        </section>

        <section v-else-if="detailTab === 'deploy'" class="detail-panel deploy-detail-panel">
          <div class="detail-subtabs">
            <button :class="{ active: deployMode === 'online' }" type="button" @click="setDeployMode('online')">在线服务化部署</button>
            <button :class="{ active: deployMode === 'offline' }" type="button" @click="setDeployMode('offline')">离线部署</button>
          </div>

          <label v-if="deployMode === 'online'" class="deploy-field">
            <span class="required-label">服务名称：</span>
            <el-input v-model="deployForm.serviceName" placeholder="请输入" />
          </label>

          <section class="deploy-section">
            <h3>请选择部署权重</h3>
            <p>选择本产线训练生成的模型权重，或与当前任务匹配的官方预训练权重</p>

            <template v-if="deployMode === 'online'">
              <div class="weight-source-switch" role="group" aria-label="部署权重来源">
                <button
                  type="button"
                  :class="{ active: deployForm.weightSource === 'pipeline' }"
                  data-testid="deployment-weight-pipeline"
                  @click="selectDeploymentWeightSource('pipeline')"
                >
                  本产线模型权重
                </button>
                <button
                  type="button"
                  :class="{ active: deployForm.weightSource === 'official' }"
                  data-testid="deployment-weight-official"
                  @click="selectDeploymentWeightSource('official')"
                >
                  官方预训练权重
                </button>
              </div>
              <div class="deployment-weight-row required-row">
                <span>模型权重：</span>
                <el-select v-model="deployForm.weight" class="detail-select" placeholder="请选择模型权重">
                  <el-option
                    v-for="option in deployWeightOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
              </div>
              <el-empty v-if="deployWeightOptions.length === 0" description="当前来源暂无可用权重" :image-size="56" />
            </template>

            <div v-else class="offline-model-row required-row">
              <span>{{ taskLabel(detailPipeline.task) }}：</span>
              <el-select v-model="deployForm.weight" placeholder="请选择模型权重" data-testid="offline-weight-select">
                <el-option
                  v-for="option in detailWeightOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </div>
          </section>

          <template v-if="deployMode === 'online'">
            <section class="edge-resource-section" aria-label="边缘资源选择">
              <h3>选择边缘资源</h3>
              <span class="legacy-environment-label">选择环境：</span>
              <p v-if="edgeResourcesLoading">正在读取资源池与节点...</p>
              <p v-else-if="edgeResourceError" class="deploy-error">{{ edgeResourceError }}</p>
              <template v-else>
                <div class="edge-choice-row">
                  <span class="required-label">资源池：</span>
                  <button
                    v-for="pool in enabledResourcePools"
                    :key="pool.id"
                    class="edge-choice"
                    :class="{ selected: deployForm.poolId === pool.id }"
                    type="button"
                    @click="selectDeploymentPool(pool.id)"
                  >
                    <strong>{{ pool.name }}</strong>
                    <small>{{ platformLabel(pool.kind) }} · {{ poolCompatibility(pool) }}</small>
                  </button>
                </div>
                <div class="edge-choice-row">
                  <span class="required-label">边缘节点：</span>
                  <button
                    v-for="node in deploymentNodeOptions"
                    :key="node.id"
                    class="edge-choice node-choice"
                    :class="{ selected: deployForm.nodeId === node.id }"
                    type="button"
                    @click="selectDeploymentNode(node.id)"
                  >
                    <strong>{{ node.name }}</strong>
                    <small>{{ node.status === "online" ? "在线" : node.status }} · {{ nodeResourceSummary(node) }}</small>
                  </button>
                </div>
              </template>
            </section>

            <section class="optimization-section">
              <h3>推理优化</h3>
              <div class="optimization-modes">
                <button
                  type="button"
                  :class="{ active: deployForm.optimizationMode === 'auto' }"
                  data-testid="optimization-auto"
                  @click="deployForm.optimizationMode = 'auto'"
                >
                  <strong>自动优化</strong>
                  <span>根据节点能力自动选择 TensorRT FP16</span>
                </button>
                <button
                  type="button"
                  :class="{ active: deployForm.optimizationMode === 'manual' }"
                  data-testid="optimization-manual"
                  @click="deployForm.optimizationMode = 'manual'"
                >
                  <strong>手动配置</strong>
                  <span>覆盖格式、精度与输入尺寸</span>
                </button>
              </div>
              <div v-if="deployForm.optimizationMode === 'auto'" class="optimization-summary">
                <strong>推荐方案</strong><span>TensorRT FP16</span>
              </div>
              <div v-else class="manual-optimization-grid">
                <label><span>导出格式</span><select v-model="deployForm.format"><option value="pt">PyTorch</option><option value="onnx">ONNX</option><option value="engine">TensorRT</option></select></label>
                <label><span>推理精度</span><select v-model="deployForm.precision"><option value="fp32">FP32</option><option value="fp16">FP16</option><option value="int8">INT8</option></select></label>
                <label><span>输入尺寸</span><input v-model.number="deployForm.inputSize" type="number" min="32" step="32" /></label>
                <label><span>INT8 校准数据集</span><input v-model="deployForm.calibrationDatasetUri" placeholder="minio://datasets/calibration" /></label>
              </div>
            </section>

            <div class="env-note">
              <strong>环境分配：</strong>
              <span>{{ selectedDeploymentNode ? nodeResourceSummary(selectedDeploymentNode) : "请先选择在线节点" }}</span>
              <template v-if="selectedDeploymentNode">
                <el-input v-model="deployForm.instanceName" class="instance-name-input" placeholder="请输入实例名称" maxlength="160" />
                <span>实例名称</span>
              </template>
            </div>
            <el-button class="deploy-action" type="primary" :disabled="!canDeploy" :loading="deploying" @click="startDeployment">
              开始部署
            </el-button>
          </template>
          <el-button v-else class="deploy-action offline-export-button" type="primary" :disabled="!deployForm.weight" @click="exportOfflineModel">
            导出模型文件
          </el-button>
        </section>

        <section v-else class="detail-panel evaluate-detail-panel">
          <div class="detail-subtabs">
            <button :class="{ active: evaluationTab === 'pipeline' }" type="button" @click="openEvaluationSubtab('pipeline')">产线评估</button>
            <button :class="{ active: evaluationTab === 'history' }" type="button" @click="openEvaluationSubtab('history')">评估历史</button>
          </div>
          <template v-if="evaluationTab === 'pipeline'">
            <h3>模型验证集评估结果</h3>
            <table class="evaluation-table">
              <thead><tr><th>模型权重</th><th>验证集分数</th><th>标记模型权重</th></tr></thead>
              <tbody>
                <tr v-for="row in evaluationRows" :key="row.weight">
                  <td>{{ row.weight }}</td>
                  <td>{{ row.score }}</td>
                  <td>
                    <button class="table-link" type="button" @click="openWeightMarkDialog(row)">
                      标记权重
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <h3>创建评估任务</h3>
            <div class="evaluation-form">
              <div class="evaluation-choice-row">
                <span class="evaluation-choice-label">* 选择评估集 <small class="help-dot">?</small></span>
                <el-radio-group v-model="evaluationForm.dataset" class="evaluation-radio-group">
                  <el-radio label="val">验证集</el-radio>
                  <el-radio label="custom">自定义测试集</el-radio>
                </el-radio-group>
                <em v-if="evaluationForm.dataset === 'custom'" class="evaluation-tip">
                  <small class="help-dot">i</small> 请确保数据包名称与数据集根目录名称保持一致，数据集格式详细要求
                </em>
              </div>
              <div v-if="evaluationForm.dataset === 'custom'" class="custom-dataset-picker">
                <button
                  :class="{ active: evaluationDatasetTab === 'validated' }"
                  type="button"
                  @click="openEvaluationDatasetDialog('validated')"
                >
                  <span class="dataset-folder-icon">▣</span>
                  <strong>已校验数据集</strong>
                  <small>{{ validatedEvaluationDatasetCount }} 个</small>
                </button>
                <button
                  :class="{ active: evaluationDatasetTab === 'unvalidated' }"
                  type="button"
                  @click="openEvaluationDatasetDialog('unvalidated')"
                >
                  <span class="dataset-folder-icon">▣</span>
                  <strong>未校验数据集</strong>
                  <small>{{ unvalidatedEvaluationDatasetCount }} 个</small>
                </button>
              </div>
              <p v-if="evaluationForm.dataset === 'custom'" class="selected-evaluation-dataset">
                已选数据集：<strong>{{ selectedEvaluationDataset?.name || "未选择" }}</strong>
              </p>
              <label class="evaluation-row">
                <span>* 选择模型权重</span>
                <el-select v-model="evaluationForm.weight" placeholder="请选择模型权重">
                  <el-option v-for="option in detailWeightOptions" :key="option.value" :label="option.label" :value="option.value" />
                </el-select>
              </label>
              <label class="evaluation-row">
                <span>* 选择环境</span>
                <el-select v-model="evaluationForm.environment" placeholder="请选择环境">
                  <el-option label="CPU" value="cpu" />
                  <el-option label="gpu节点_1" value="0" />
                </el-select>
              </label>
              <el-button type="primary" :loading="evaluationLoading" @click="startPipelineEvaluation">开始评估</el-button>
            </div>
          </template>

          <div v-else class="evaluation-history-panel">
            <aside class="evaluation-history-list">
              <h3>请选择评估记录</h3>
              <button
                v-for="record in evaluationHistory"
                :key="record.id"
                :class="{ active: selectedEvaluationHistoryId === record.id }"
                type="button"
                @click="selectedEvaluationHistoryId = record.id"
              >
                {{ formatEvaluationRecordTitle(record) }}
              </button>
              <p v-if="!evaluationHistoryLoading && evaluationHistory.length === 0">暂无评估记录</p>
              <p v-if="evaluationHistoryLoading">正在加载...</p>
            </aside>
            <section class="evaluation-history-detail">
              <template v-if="selectedEvaluationHistory">
                <h3>日志详情：</h3>
                <dl>
                  <dt>评估任务状态：</dt><dd class="history-status">{{ evaluationStatusLabel(selectedEvaluationHistory.status) }}</dd>
                  <dt>1. 评估模型：</dt><dd>{{ detailModelName }}</dd>
                  <dt>2. 选择评估集：</dt><dd>{{ evaluationDatasetName(selectedEvaluationHistory.dataset_id) }}</dd>
                  <dt>3. 选择模型权重：</dt><dd>{{ selectedEvaluationHistory.model_weight }}</dd>
                  <dt>4. 选择环境：</dt><dd>{{ selectedEvaluationHistory.environment }}</dd>
                  <dt>5. 评估分数：</dt><dd>AP:{{ formatMetricValue(selectedEvaluationHistory.score) }}</dd>
                </dl>
                <h3>Ultralytics 指标：</h3>
                <table class="history-metrics-table">
                  <tbody>
                    <tr v-for="metric in selectedEvaluationMetricRows" :key="metric.label">
                      <th>{{ metric.label }}</th>
                      <td>{{ metric.value }}</td>
                    </tr>
                  </tbody>
                </table>
              </template>
              <p v-else class="history-empty">请选择左侧评估记录</p>
            </section>
          </div>
        </section>
      </section>
    </template>

    <template v-else>
      <PipelineWizardShell
        :steps="wizardSteps"
        :active-step="activeStep"
        :submitting="submitting"
        :show-direct-deploy="!isLlmWizard && !isPendingCapabilityWizard"
        :show-save-draft="!isPendingCapabilityWizard"
        :next-disabled="isPendingCapabilityWizard"
        :draft-saving="wizardDraftSaving"
        :draft-status="wizardDraftStatus"
        @back="backToList"
        @step="goToWizardStep"
        @previous="activeStep -= 1"
        @next="goNext"
        @submit="submitTraining"
        @save-draft="saveWizardDraftFromAction"
      >
        <LlmPipelineWizardSteps
          v-if="isLlmWizard"
          ref="llmWizardRef"
          v-model="llmForm"
          :active-step="activeStep"
          :datasets="datasets"
          :resource-pools="resourcePools"
          :compute-nodes="computeNodes"
          :resources-loading="edgeResourcesLoading"
          :resource-error="edgeResourceError"
          @request-resources="loadEdgeResources"
          @dataset-uploaded="handleLlmDatasetUploaded"
        />

        <template v-else>
        <div v-if="activeStep === 0" class="step-panel framework-step">
          <header class="framework-step__heading">
            <div>
              <h2>选择训练框架</h2>
              <p>根据当前任务选择训练引擎，模型将在下一步配置。</p>
            </div>
            <span>{{ taskLabel(form.task) }}</span>
          </header>
          <section v-if="isPendingCapabilityWizard" class="pending-capability" data-testid="pending-capability">
            <strong>{{ taskLabel(form.task) }}训练能力待接入</strong>
            <p>产线草稿已创建并保存在模型空间。当前版本暂不提供该任务的训练框架、模型和参数配置，能力接入后可继续配置。</p>
          </section>
          <div v-else-if="frameworkCatalog" class="framework-step__layout">
            <FrameworkModelSelector
              mode="framework"
              :catalog="frameworkCatalog"
              :selection="frameworkSelection"
              :locked="Boolean(activeWizardPipeline?.framework_locked_at)"
              :lock-reason="frameworkLockReason"
              @update:selection="setFrameworkSelection"
              @clone="cloneWithFrameworkSelection"
            />
            <aside class="pipeline-summary" aria-label="当前产线摘要">
              <span class="pipeline-summary__eyebrow">当前产线</span>
              <strong>{{ form.name || selectedScenario.label }}</strong>
              <p>{{ selectedScenario.description }}</p>
              <dl>
                <div><dt>任务场景</dt><dd>{{ taskLabel(form.task) }}</dd></div>
                <div><dt>模型配置</dt><dd>下一步选择</dd></div>
              </dl>
            </aside>
          </div>
        </div>

        <div v-else-if="activeStep === 1" class="step-panel data-step">
          <div class="step-main">
            <h2>请选择模型并添加数据集</h2>
            <label class="field-label required">选择模型</label>
            <el-cascader
              v-model="modelCascaderValue"
              class="full-input"
              data-testid="framework-model-cascader"
              :options="modelCascaderOptions"
              :props="{ expandTrigger: 'hover' }"
              placeholder="请选择当前任务与框架支持的模型"
            />

            <label class="field-label required">添加数据集</label>
            <div class="dataset-picker">
              <button type="button" class="dataset-tab active">已校验数据集</button>
              <el-select v-model="form.dataset_id" class="dataset-select" placeholder="请选择数据集" @change="updateClassCountFromDataset">
                <el-option
                  v-for="dataset in selectableDatasets"
                  :key="dataset.id"
                  :label="dataset.name"
                  :value="dataset.id"
                />
              </el-select>
            </div>
            <span v-if="selectedDataset" class="selected-chip">{{ selectedDataset.name }}</span>

            <div class="processing-box">
              <div class="processing-title">
                <el-checkbox v-model="splitEnabled">数据切分</el-checkbox>
                <span>训练集 {{ form.train_ratio }}% / 验证集 {{ form.val_ratio }}% / 测试集 {{ form.test_ratio }}%</span>
              </div>
              <el-slider v-model="form.train_ratio" :min="50" :max="90" @change="rebalanceSplit" />
            </div>

            <section class="analysis-box">
              <h3>数据分析与可视化结果</h3>
              <div class="analysis-summary">
                <span>数据校验通过</span>
                <span>训练集：{{ trainCount }} 个样本，占比{{ form.train_ratio }}%</span>
                <span>验证集：{{ valCount }} 个样本，占比{{ form.val_ratio }}%</span>
                <span>类别数量：{{ form.class_num }}个</span>
              </div>
              <div class="analysis-tabs">
                <button :class="{ active: processingTab === 'train' }" type="button" @click="processingTab = 'train'">训练集</button>
                <button :class="{ active: processingTab === 'val' }" type="button" @click="processingTab = 'val'">验证集</button>
                <button :class="{ active: processingTab === 'test' }" type="button" @click="processingTab = 'test'">测试集</button>
                <button :class="{ active: processingTab === 'classes' }" type="button" @click="processingTab = 'classes'">类别分布图</button>
              </div>
              <div v-if="processingLoading" class="sample-preview loading-preview">正在加载数据分析...</div>
              <div v-else-if="processingTab !== 'classes'" class="sample-preview">
                <div class="sample-canvas">
                  <img
                    v-if="activePreviewSample"
                    :src="sampleImageUrl(activePreviewSample)"
                    :alt="activePreviewSample.id"
                  />
                  <el-empty v-else description="暂无样本" />
                </div>
                <div class="thumbnail-row">
                  <button
                    v-for="sample in visibleSamples"
                    :key="sample.id"
                    class="thumb"
                    :class="{ active: activePreviewSample?.id === sample.id }"
                    type="button"
                    @click="activeSampleId = sample.id"
                  >
                    <img :src="sampleImageUrl(sample)" :alt="sample.id" />
                  </button>
                </div>
              </div>
              <section v-else class="class-chart">
                <div v-if="classChartItems.length > 0" class="chart-shell">
                  <div class="chart-legend">
                    <span><i class="train"></i>train</span>
                    <span><i class="val"></i>val</span>
                  </div>
                  <div class="chart-frame">
                    <div class="chart-y-axis">
                      <span v-for="tick in chartTicks" :key="tick">{{ tick }}</span>
                    </div>
                    <div class="chart-plot" :style="{ '--class-count': classChartItems.length }">
                      <div class="chart-grid">
                        <span v-for="tick in chartTicks" :key="tick"></span>
                      </div>
                      <div v-for="item in classChartItems" :key="item.name" class="bar-group">
                        <div class="bar-pair">
                          <i class="bar train" :style="{ height: `${barPercent(item.train)}%` }" :title="`train: ${item.train}`"></i>
                          <i class="bar val" :style="{ height: `${barPercent(item.val)}%` }" :title="`val: ${item.val}`"></i>
                        </div>
                        <span class="chart-x-label">{{ item.name }}</span>
                      </div>
                    </div>
                  </div>
                </div>
                <el-empty v-else description="暂无类别统计" />
              </section>
            </section>
          </div>

          <aside class="step-aside">
            <h3>{{ selectedModelName }}</h3>
            <p>基于 YOLO26 的视觉训练产线，支持在完成数据校验后直接绑定数据集、调整切分比例并提交训练。</p>
            <table>
              <tbody>
                <tr><th>任务类型</th><td>{{ taskLabel(form.task) }}</td></tr>
                <tr><th>模型文件</th><td>{{ selectedModelName }}</td></tr>
                <tr><th>数据集</th><td>{{ selectedDataset?.name || "待选择" }}</td></tr>
              </tbody>
            </table>
          </aside>
        </div>

        <div v-else-if="activeStep === 2" class="step-panel params-step">
          <ObjectDetectionTrainingConfig
            v-if="selectedFrameworkModel"
            v-model="frameworkParameters"
            :model="selectedFrameworkModel"
            :parameters="selectedFrameworkTask?.parameters ?? []"
            @update:valid="frameworkConfigValid = $event"
          />
          <el-alert v-else type="error" :closable="false" title="当前框架没有可配置的模型" />
        </div>

        <div v-else class="step-panel submit-step">
          <h2>请选择训练环境</h2>
          <div class="training-target-switch" role="group" aria-label="训练运行位置">
            <button
              type="button"
              :class="{ active: trainingEnvironment.mode === 'local' }"
              data-testid="training-target-local"
              @click="selectTrainingTarget('local')"
            >
              <strong>本机 CPU</strong>
              <span>使用平台本地训练 Worker</span>
            </button>
            <button
              type="button"
              :class="{ active: trainingEnvironment.mode === 'remote' }"
              data-testid="training-target-remote"
              @click="selectTrainingTarget('remote')"
            >
              <strong>远程 GPU</strong>
              <span>使用已接入的边缘服务器</span>
            </button>
          </div>

          <div v-if="trainingEnvironment.mode === 'local'" class="local-training-summary">
            <span class="resource-status-dot online"></span>
            <div><strong>CPU</strong><small>device=cpu · workers=2</small></div>
          </div>

          <section v-else class="training-resource-section" aria-label="远程训练资源选择">
            <p v-if="edgeResourcesLoading">正在读取服务器 GPU 资源...</p>
            <p v-else-if="edgeResourceError" class="deploy-error">{{ edgeResourceError }}</p>
            <template v-else>
              <div class="training-resource-group">
                <div class="training-resource-heading"><strong>资源池</strong><span>同一次训练只使用同类兼容设备</span></div>
                <div v-if="trainingResourcePools.length" class="training-resource-options">
                  <button
                    v-for="pool in trainingResourcePools"
                    :key="pool.id"
                    type="button"
                    class="training-resource-card"
                    :class="{ selected: trainingEnvironment.poolId === pool.id }"
                    :data-testid="`training-pool-${pool.id}`"
                    @click="selectTrainingPool(pool.id)"
                  >
                    <strong>{{ pool.name }}</strong>
                    <small>{{ platformLabel(pool.kind) }} · {{ poolCompatibility(pool) }}</small>
                  </button>
                </div>
                <el-empty v-else description="暂无可用的 GPU 资源池" :image-size="64" />
              </div>

              <div class="training-resource-group">
                <div class="training-resource-heading"><strong>服务器与 GPU</strong><span>仅显示在线且 GPU 可用的节点</span></div>
                <div v-if="trainingNodeOptions.length" class="training-resource-options">
                  <button
                    v-for="node in trainingNodeOptions"
                    :key="node.id"
                    type="button"
                    class="training-resource-card training-node-card"
                    :class="{ selected: trainingEnvironment.nodeId === node.id }"
                    :data-testid="`training-node-${node.id}`"
                    @click="selectTrainingNode(node.id)"
                  >
                    <span class="resource-card-title">
                      <span class="resource-status-dot online"></span>
                      <strong>{{ node.name }}</strong>
                      <em>在线</em>
                    </span>
                    <small>{{ nodeResourceSummary(node) }}</small>
                    <small>{{ platformLabel(node.platform_kind) }} · {{ node.architecture }}</small>
                  </button>
                </div>
                <el-empty v-else description="当前资源池没有在线 GPU 服务器" :image-size="64" />
              </div>

              <div v-if="selectedTrainingNode" class="training-allocation-row">
                <div>
                  <span>GPU 数量</span>
                  <small>该服务器可用 {{ selectedTrainingNodeGpuCount }} 张</small>
                </div>
                <el-input-number
                  v-model="trainingEnvironment.requestedGpus"
                  :min="1"
                  :max="selectedTrainingNodeGpuCount"
                  data-testid="training-gpu-count"
                />
              </div>

              <details class="training-runtime-config">
                <summary>运行镜像配置</summary>
                <label>
                  <span class="required-label">训练镜像摘要</span>
                  <el-input
                    v-model="trainingEnvironment.imageDigest"
                    placeholder="registry.example/visiox/yolo26-training@sha256:..."
                    data-testid="training-image-digest"
                  />
                  <small>远程服务器将拉取该不可变镜像；可通过 VITE_REMOTE_TRAINING_IMAGE_DIGEST 预配置。</small>
                </label>
              </details>
            </template>
          </section>
          <div class="submit-summary">
            <span>产线名称：{{ form.name }}</span>
            <span>任务类型：{{ taskLabel(form.task) }}</span>
            <span>基础模型：{{ selectedModelName }}</span>
            <span>数据集：{{ selectedDataset?.name || "待选择" }}</span>
            <span>训练位置：{{ trainingEnvironmentLabel }}</span>
          </div>
        </div>
        </template>

      </PipelineWizardShell>
    </template>

    <el-dialog
      v-model="resultFilesDialogVisible"
      title="结果文件"
      width="720px"
      class="result-files-dialog"
    >
      <div class="result-files-table" role="table" aria-label="训练结果文件">
        <div class="result-files-row result-files-header" role="row">
          <span role="columnheader">文件名称</span>
          <span role="columnheader">大小</span>
          <span role="columnheader">操作</span>
        </div>
        <div v-for="(file, index) in resultFiles" :key="`${file.kind}-${file.name}`" class="result-files-row" role="row">
          <span role="cell">{{ file.name }}</span>
          <span role="cell">{{ formatFileSize(file.size_bytes) }}</span>
          <a
            role="cell"
            :href="resultFileDownloadUrl(file)"
            :download="file.name"
            :data-testid="`download-result-${index}`"
          >
            下载
          </a>
        </div>
        <p v-if="resultFilesLoading" class="result-files-empty">正在加载结果文件...</p>
        <p v-else-if="resultFiles.length === 0" class="result-files-empty">暂无可下载的结果文件</p>
      </div>
    </el-dialog>

    <el-dialog
      v-model="evaluationDatasetDialogVisible"
      :title="evaluationDatasetDialogTitle"
      width="704px"
      class="evaluation-dataset-dialog"
    >
      <div class="evaluation-dataset-tabs">
        <button
          :class="{ active: evaluationDatasetSourceTab === 'sample' }"
          type="button"
          @click="evaluationDatasetSourceTab = 'sample'"
        >
          样例数据集
        </button>
        <button
          :class="{ active: evaluationDatasetSourceTab === 'personal' }"
          type="button"
          @click="evaluationDatasetSourceTab = 'personal'"
        >
          个人数据集
        </button>
      </div>

      <div class="evaluation-dataset-list">
        <button
          v-for="dataset in evaluationDialogDatasetOptions"
          :key="dataset.id"
          class="evaluation-dataset-row"
          :class="{ selected: pendingEvaluationDatasetId === dataset.id }"
          type="button"
          @click="pendingEvaluationDatasetId = dataset.id"
        >
          <span class="dataset-check"></span>
          <span class="dataset-avatar">{{ datasetBadgeText(dataset.name) }}</span>
          <span class="dataset-name">{{ dataset.name }}</span>
        </button>
        <p v-if="evaluationDialogDatasetOptions.length === 0" class="evaluation-dataset-empty">
          暂无可选数据集
        </p>
      </div>

      <template #footer>
        <el-button
          type="primary"
          :disabled="!pendingEvaluationDatasetId"
          @click="confirmEvaluationDatasetSelection"
        >
          添加({{ pendingEvaluationDatasetId ? 1 : 0 }}/1)
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="weightMarkDialogVisible"
      width="420px"
      class="weight-mark-dialog"
      :show-close="false"
    >
      <p class="weight-mark-title">请为模型权重命名，以便于在部署环节快速找到它</p>
      <el-input v-model="weightMarkName" maxlength="64" placeholder="请输入权重名称" />
      <p class="weight-mark-tip">
        提示：名称最多64个字符，支持中英文、数字、下划线、中划线，且符号不能在首尾，命名示例：“数据集A评估最佳”
      </p>
      <template #footer>
        <el-button plain @click="weightMarkDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="weightMarkLoading" @click="confirmWeightMark">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createDialogVisible" title="创建产线" width="760px" class="create-dialog">
      <div class="dialog-tabs">
        <button :class="{ active: createTab === 'zero' }" type="button" @click="createTab = 'zero'">零代码产线</button>
        <button :class="{ active: createTab === 'local' }" type="button" @click="createTab = 'local'">本地模型</button>
      </div>

      <div v-if="createTab === 'zero'" class="create-form">
        <label class="field-label required">产线名称</label>
        <el-input v-model="createForm.name" placeholder="请输入产线名称" />
        <label class="field-label required">任务场景</label>
        <div class="scenario-grid">
          <button
            v-for="scenario in scenarios"
            :key="scenario.key"
            class="scenario-option"
            :class="{ selected: createForm.scenarioKey === scenario.key }"
            type="button"
            @click="selectScenario(scenario.key)"
          >
            <span class="scenario-copy">
              <strong>{{ scenario.label }}</strong>
              <span>{{ scenario.description }}</span>
            </span>
            <span class="scenario-icon" :class="scenario.tone" aria-hidden="true">
              <el-icon><component :is="scenario.icon" /></el-icon>
            </span>
          </button>
        </div>
      </div>

      <div v-else class="create-form local-form">
        <label class="field-label required">产线名称</label>
        <el-input v-model="createForm.name" placeholder="请输入产线名称" />
        <div class="local-model-fields">
          <div>
            <label class="field-label required">任务类型</label>
            <el-select v-model="createForm.localTask" class="full-input" aria-label="本地模型任务类型">
              <el-option v-for="option in localModelTaskOptions" :key="option.value" :label="option.label" :value="option.value" />
            </el-select>
          </div>
          <div>
            <label class="field-label required">模型规格</label>
            <el-select v-model="createForm.localScale" class="full-input" aria-label="本地模型规格">
              <el-option v-for="scale in ['n', 's', 'm', 'l', 'x']" :key="scale" :label="scale.toUpperCase()" :value="scale" />
            </el-select>
          </div>
        </div>
        <label class="field-label required">模型权重</label>
        <input ref="modelFileInput" class="visually-hidden" type="file" accept=".pt" data-testid="local-model-file" @change="selectLocalModelFile" />
        <button class="upload-box" :class="{ selected: localModelFile }" type="button" @click="modelFileInput?.click()">
          <span class="upload-icon"><el-icon><UploadFilled /></el-icon></span>
          <span class="upload-copy">
            <strong>{{ localModelFile?.name || '点击上传模型权重' }}</strong>
            <small>{{ localModelFile ? formatFileSize(localModelFile.size) : '支持 Ultralytics / PyTorch .pt，单文件最大 2 GB' }}</small>
          </span>
        </button>
        <p class="local-model-hint">ONNX 与 TensorRT Engine 用于部署，不能作为继续训练的基础权重。</p>
        <label class="field-label">标签</label>
        <el-input v-model="createForm.tags" placeholder="请输入标签" />
        <label class="field-label">简介摘要</label>
        <el-input v-model="createForm.description" type="textarea" :rows="4" placeholder="请输入简介摘要" />
      </div>

      <template #footer>
        <el-button type="primary" :loading="submitting" data-testid="confirm-create-pipeline" @click="startWizard">
          {{ createTab === "zero" ? "创建产线" : "确认创建" }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="renameDialogVisible" title="修改名称" width="420px">
      <el-input v-model="renameName" placeholder="请输入产线名称" />
      <template #footer>
        <el-button plain @click="renameDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmRename">确认</el-button>
      </template>
    </el-dialog>

    <ResourceSharingDialog
      v-if="sharingPipeline"
      v-model="publicDialogVisible"
      resource-type="pipeline"
      :resource-id="sharingPipeline.id"
      :resource-name="sharingPipeline.name"
      @saved="handlePipelineSharingSaved"
    />
  </section>
</template>

<script setup lang="ts">
import {
  Aim,
  ArrowLeft,
  Check,
  CircleClose,
  Cpu,
  Crop,
  Document,
  Download,
  EditPen,
  Grid,
  Loading,
  MoreFilled,
  Picture,
  PriceTag,
  Refresh,
  Search,
  Star,
  StarFilled,
  Timer,
  UploadFilled,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, onUnmounted, reactive, ref, watch, type Component } from "vue";
import { useRoute, useRouter } from "vue-router";
import { parse as parseYaml } from "yaml";

import {
  api,
  type BaseModelRecord,
  type DatasetRecord,
  type DatasetSampleRecord,
  type PipelineEvaluationResponse,
  type PipelinePredictResponse,
  type TrainingArtifactRecord,
  type TrainedModelRecord,
  type TrainingJobRecord,
  type TrainingPipelineRecord,
  type ComputeNodeRecord,
  type FrameworkCapabilityCatalogResponse,
  type ResourcePoolRecord,
} from "@/api/client";
import type { ExperienceInferenceRequest } from "@/components/ServiceExperiencePanel.vue";
import ServiceExperiencePanel from "@/components/ServiceExperiencePanel.vue";
import ResourceSharingDialog from "@/components/sharing/ResourceSharingDialog.vue";
import PipelineWizardShell from "@/features/pipeline-wizard/PipelineWizardShell.vue";
import FrameworkModelSelector from "@/features/pipeline-wizard/FrameworkModelSelector.vue";
import ObjectDetectionTrainingConfig from "@/features/pipeline-wizard/ObjectDetectionTrainingConfig.vue";
import { usePipelineWizardDraft } from "@/features/pipeline-wizard/usePipelineWizardDraft";
import {
  compatibleFrameworks,
  defaultFrameworkModelSelection,
  legacyEngineForFramework,
  pipelineTaskForTaskKind,
  taskForFramework,
  taskKindForPipelineTask,
  type FrameworkModelSelection,
} from "@/features/pipeline-wizard/frameworkCatalog";
import LlmPipelineWizardSteps from "@/features/pipeline-wizard/llm/LlmPipelineWizardSteps.vue";
import {
  createDefaultLlmTrainingForm,
  toLlamaFactoryConfig,
  type LlmTrainingForm,
} from "@/features/pipeline-wizard/llm/llmTrainingForm";

type ActiveTab = "all" | "mine" | "favorite";
type ViewMode = "list" | "wizard" | "detail";
type CreateTab = "zero" | "local";
type ProcessingTab = "train" | "val" | "test" | "classes";
type DetailTab = "basic" | "logs" | "experience" | "deploy" | "evaluate";
type LlmWizardExpose = {
  validateStep: (step: number) => { valid: boolean; message: string };
};

type DatasetAnalysis = {
  sample_count?: number;
  annotation_count?: number;
  class_distribution?: Record<string, number>;
  class_distribution_by_split?: Record<string, Record<string, number>>;
  split_distribution?: Record<string, number>;
};

type Scenario = {
  key: string;
  label: string;
  task: string;
  description: string;
  icon: Component;
  tone: "blue" | "green";
};

type WeightOption = {
  label: string;
  value: string;
  modelId?: string;
  baseModelId?: string;
  modelName?: string;
  weightName?: string;
  deploymentName?: string;
};

type EvaluationRow = {
  weight: string;
  sourceWeight: string;
  score: string;
  modelId?: string;
  deploymentName?: string;
};

type TrainForm = {
  name: string;
  task: string;
  scale: string;
  base_model_id: string;
  dataset_id: string;
  train_ratio: number;
  val_ratio: number;
  test_ratio: number;
  epochs: number;
  batch: number;
  class_num: number;
  lr0: string;
  log_interval: number;
  resume_weight: string;
  pretrained: string;
  warmup_epochs: number;
  save_period: number;
  eval_interval: number;
  image_size: number;
  workers: number;
  amp: boolean;
  device: string;
};

type TrainingEnvironmentMode = "local" | "remote";

const router = useRouter();
const route = useRoute();

const tabOptions: Array<{ label: string; value: ActiveTab }> = [
  { label: "全部产线", value: "all" },
  { label: "我创建的", value: "mine" },
  { label: "我收藏的", value: "favorite" },
];

const scenarios: Scenario[] = [
  { key: "detect", label: "目标检测", task: "detect", description: "从图像或视频中定位并识别目标对象", icon: Aim, tone: "blue" },
  { key: "doc", label: "文档图像信息抽取", task: "document", description: "分析文档版面并提取关键字段信息", icon: Document, tone: "blue" },
  { key: "ocr", label: "OCR", task: "ocr", description: "检测并识别图片、扫描件中的文字", icon: EditPen, tone: "blue" },
  { key: "table", label: "通用表格识别", task: "table", description: "识别表格区域、结构与单元格内容", icon: Grid, tone: "green" },
  { key: "classify", label: "图像分类", task: "classify", description: "按照图像特征划分单标签或多标签类别", icon: Picture, tone: "blue" },
  { key: "timeseries", label: "时序分析", task: "timeseries", description: "分析时间序列趋势、周期与异常", icon: Timer, tone: "blue" },
  { key: "segment", label: "图像分割", task: "segment", description: "对目标像素区域进行实例级分割", icon: Crop, tone: "green" },
  { key: "attribute", label: "属性识别", task: "attribute", description: "识别目标对象的多维属性标签", icon: PriceTag, tone: "blue" },
  { key: "llm", label: "大模型训练", task: "llm", description: "完成大模型偏好对齐与参数微调", icon: Cpu, tone: "blue" },
];
const implementedZeroCodeTasks = new Set(["detect", "llm"]);

const localModelTaskOptions = [
  { label: "目标检测", value: "detect" },
  { label: "图像分割", value: "segment" },
  { label: "语义分割", value: "semantic" },
  { label: "姿态估计", value: "pose" },
  { label: "旋转框检测", value: "obb" },
  { label: "图像分类", value: "classify" },
];

const managedYoloParams = new Set(["task", "mode", "model", "data", "project", "name", "exist_ok", "device", "workers"]);
const yoloParamDefaults: Record<string, unknown> = {
  epochs: 100,
  time: null,
  patience: 100,
  batch: 16,
  imgsz: 640,
  save: true,
  save_period: -1,
  cache: false,
  pretrained: true,
  optimizer: "auto",
  verbose: true,
  seed: 0,
  deterministic: true,
  single_cls: false,
  rect: false,
  cos_lr: false,
  close_mosaic: 10,
  resume: false,
  amp: true,
  fraction: 1.0,
  profile: false,
  freeze: null,
  multi_scale: 0.0,
  compile: false,
  overlap_mask: true,
  mask_ratio: 4,
  dropout: 0.0,
  val: true,
  split: "val",
  save_json: false,
  conf: null,
  iou: 0.7,
  max_det: 300,
  quantize: null,
  dnn: false,
  plots: true,
  end2end: null,
  source: null,
  vid_stride: 1,
  stream_buffer: false,
  visualize: false,
  augment: false,
  agnostic_nms: false,
  classes: null,
  retina_masks: false,
  embed: null,
  show: false,
  save_frames: false,
  save_txt: false,
  save_conf: false,
  save_crop: false,
  show_labels: true,
  show_conf: true,
  show_boxes: true,
  line_width: null,
  format: "torchscript",
  keras: false,
  optimize: false,
  dynamic: false,
  simplify: true,
  opset: null,
  workspace: null,
  nms: false,
  lr0: 0.01,
  lrf: 0.01,
  momentum: 0.937,
  weight_decay: 0.0005,
  warmup_epochs: 3.0,
  warmup_momentum: 0.8,
  warmup_bias_lr: 0.1,
  distill_model: null,
  dis: 6.0,
  box: 7.5,
  cls: 0.5,
  cls_pw: 0.0,
  dfl: 1.5,
  pose: 12.0,
  kobj: 1.0,
  rle: 1.0,
  angle: 1.0,
  nbs: 64,
  hsv_h: 0.015,
  hsv_s: 0.7,
  hsv_v: 0.4,
  degrees: 0.0,
  translate: 0.1,
  scale: 0.5,
  shear: 0.0,
  perspective: 0.0,
  flipud: 0.0,
  fliplr: 0.5,
  bgr: 0.0,
  mosaic: 1.0,
  mixup: 0.0,
  cutmix: 0.0,
  copy_paste: 0.0,
  copy_paste_mode: "flip",
  auto_augment: "randaugment",
  erasing: 0.4,
  cfg: null,
  tracker: "tracktrack.yaml",
};
const yoloParamKeys = Object.keys(yoloParamDefaults).filter((key) => !managedYoloParams.has(key));

const viewMode = ref<ViewMode>("list");
const activeTab = ref<ActiveTab>("all");
const createTab = ref<CreateTab>("zero");
const loading = ref(false);
const submitting = ref(false);
const errorMessage = ref("");
const keyword = ref("");
const sortMode = ref("newest");
const typeFilter = ref("");
const currentPage = ref(1);
const pageSize = ref(20);
const activeStep = ref(0);
const wizardSteps = ["选择产线", "数据准备", "参数准备", "提交训练"];
const wizardStepKeys = ["overview", "data", "params", "submit"] as const;
const llmWizardRef = ref<LlmWizardExpose | null>(null);
const detailTab = ref<DetailTab>("basic");
const deployMode = ref<"online" | "offline">("online");
const evaluationTab = ref<"pipeline" | "history">("pipeline");
const batchMode = ref(false);
const createDialogVisible = ref(false);
const modelFileInput = ref<HTMLInputElement | null>(null);
const localModelFile = ref<File | null>(null);
const renameDialogVisible = ref(false);
const publicDialogVisible = ref(false);
const sharingPipeline = ref<TrainingPipelineRecord | null>(null);
const resultFilesDialogVisible = ref(false);
const resultFilesLoading = ref(false);
const resultFiles = ref<TrainingArtifactRecord[]>([]);
const deploying = ref(false);
const resourcePools = ref<ResourcePoolRecord[]>([]);
const computeNodes = ref<ComputeNodeRecord[]>([]);
const edgeResourcesLoading = ref(false);
const edgeResourceError = ref("");
const splitEnabled = ref(true);
const configMode = ref(false);
const configText = ref("");
const processingLoading = ref(false);
const processingTab = ref<ProcessingTab>("train");
const activeSampleId = ref("");
const renameName = ref("");
const editingPipelineId = ref("");
const wizardPipelineId = ref("");
const detailPipelineId = ref("");
const detailLogText = ref("");
const detailLogLoading = ref(false);
const stoppingPipelineId = ref("");
const selectedPipelineIds = ref(new Set<string>());
const pipelines = ref<TrainingPipelineRecord[]>([]);
const latestJobsByPipeline = ref<Record<string, TrainingJobRecord>>({});
const baseModels = ref<BaseModelRecord[]>([]);
const trainedModels = ref<TrainedModelRecord[]>([]);
const datasets = ref<DatasetRecord[]>([]);
const configParams = ref<Record<string, unknown>>({});
const frameworkCatalog = ref<FrameworkCapabilityCatalogResponse | null>(null);
const frameworkCatalogError = ref("");
const frameworkParameters = ref<Record<string, unknown>>({});
const frameworkConfigValid = ref(true);
const frameworkAdvancedYaml = ref("");
const frameworkSelection = ref<FrameworkModelSelection>({
  taskKind: "object_detection",
  framework: "",
  adapterKey: "",
  adapterVersion: "",
  modelKey: "",
});
let refreshTimer: number | undefined;
const analysisResult = ref<DatasetAnalysis | null>(null);
const samplesBySplit = ref<Record<"train" | "val" | "test", DatasetSampleRecord[]>>({
  train: [],
  val: [],
  test: [],
});

const createForm = reactive({
  name: "新建产线",
  scenarioKey: "detect",
  tags: "",
  description: "",
  localTask: "detect",
  localScale: "n",
});

const deployForm = reactive({
  serviceName: "",
  weightSource: "pipeline" as "pipeline" | "official",
  weight: "official",
  environment: "",
  instanceName: "",
  poolId: "",
  nodeId: "",
  optimizationMode: "auto" as "auto" | "manual",
  format: "engine" as "pt" | "onnx" | "engine",
  precision: "fp16" as "fp32" | "fp16" | "int8",
  inputSize: 640,
  calibrationDatasetUri: "",
});

const evaluationForm = reactive({
  dataset: "val",
  customDatasetId: "",
  weight: "",
  environment: "cpu",
});
const evaluationDatasetTab = ref<"validated" | "unvalidated">("validated");
const evaluationDatasetDialogVisible = ref(false);
const evaluationDatasetSourceTab = ref<"sample" | "personal">("personal");
const pendingEvaluationDatasetId = ref("");
const evaluationLoading = ref(false);
const latestEvaluation = ref<PipelineEvaluationResponse | null>(null);
const evaluationHistory = ref<PipelineEvaluationResponse[]>([]);
const evaluationHistoryLoading = ref(false);
const selectedEvaluationHistoryId = ref("");
const weightMarkDialogVisible = ref(false);
const weightMarkName = ref("");
const weightMarkLoading = ref(false);
const weightMarkModelId = ref("");

const form = reactive<TrainForm>({
  name: "新建产线",
  task: "detect",
  scale: "n",
  base_model_id: "",
  dataset_id: "",
  train_ratio: 70,
  val_ratio: 30,
  test_ratio: 0,
  epochs: 40,
  batch: 4,
  class_num: 1,
  lr0: "0.000050",
  log_interval: 10,
  resume_weight: "",
  pretrained: "official",
  warmup_epochs: 3,
  save_period: 1,
  eval_interval: 1,
  image_size: 640,
  workers: 4,
  amp: true,
  device: "cpu",
});

const llmForm = ref<LlmTrainingForm>(createDefaultLlmTrainingForm());

const trainingEnvironment = reactive({
  mode: "local" as TrainingEnvironmentMode,
  poolId: "",
  nodeId: "",
  requestedGpus: 1,
  imageDigest: String(import.meta.env.VITE_REMOTE_TRAINING_IMAGE_DIGEST ?? ""),
});

const typeOptions = computed(() => Array.from(new Set(pipelines.value.map((item) => item.task))).sort());

const selectedScenario = computed(
  () => scenarios.find((scenario) => scenario.key === createForm.scenarioKey) || scenarios[0],
);
const isPendingCapabilityWizard = computed(() => !implementedZeroCodeTasks.has(form.task));
const activeWizardPipeline = computed(() => pipelines.value.find((pipeline) => pipeline.id === wizardPipelineId.value));
const frameworkLockReason = computed(() => activeWizardPipeline.value?.framework_locked_at ? "Framework and model are locked because this pipeline has already been submitted for training." : "");
const isLlmWizard = computed(() => form.task === "llm");
const selectedFrameworkAdapter = computed(() => compatibleFrameworks(frameworkCatalog.value, frameworkSelection.value.taskKind)
  .find((adapter) => adapter.framework === frameworkSelection.value.framework));
const selectedFrameworkTask = computed(() => taskForFramework(selectedFrameworkAdapter.value, frameworkSelection.value.taskKind));
const selectedFrameworkModel = computed(() => selectedFrameworkTask.value?.models
  .find((model) => model.model_key === frameworkSelection.value.modelKey));
const isPaddleXFramework = computed(() => frameworkSelection.value.framework === "paddlex");
const requiresBaseModel = computed(() => frameworkSelection.value.framework === "ultralytics");
const modelCascaderOptions = computed(() => [{
  value: frameworkSelection.value.taskKind,
  label: taskLabel(form.task),
  children: (selectedFrameworkTask.value?.models ?? []).map((model) => ({
    value: model.model_key,
    label: model.display_name,
  })),
}]);
const modelCascaderValue = computed<string[]>({
  get: () => frameworkSelection.value.modelKey
    ? [frameworkSelection.value.taskKind, frameworkSelection.value.modelKey]
    : [],
  set: (value) => setFrameworkModel(value[value.length - 1] || ""),
});
const frameworkDraft = computed(() => ({
  selection: frameworkSelection.value,
  parameters: frameworkTrainingParams(),
}));
const frameworkDraftController = usePipelineWizardDraft(
  frameworkDraft,
  computed(() => viewMode.value === "wizard" && !isLlmWizard.value && !isPendingCapabilityWizard.value && Boolean(wizardPipelineId.value) && !activeWizardPipeline.value?.framework_locked_at),
  saveFrameworkDraft,
);
const llmDraftController = usePipelineWizardDraft(
  llmForm,
  computed(() => viewMode.value === "wizard" && isLlmWizard.value && Boolean(wizardPipelineId.value)),
  saveLlmDraft,
);
const llmDraftSaving = computed(() => llmDraftController.status.value === "saving");
const llmDraftStatus = computed(() => {
  if (llmDraftController.status.value === "dirty") return "有未保存更改";
  if (llmDraftController.status.value === "saving") return "草稿保存中";
  if (llmDraftController.status.value === "error") return "草稿保存失败，点击重试";
  const savedAt = llmDraftController.lastSavedAt.value;
  return savedAt
    ? `草稿已保存 ${savedAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`
    : "草稿已保存";
});
const frameworkDraftSaving = computed(() => frameworkDraftController.status.value === "saving");
const frameworkDraftStatus = computed(() => {
  if (frameworkDraftController.status.value === "dirty") return "有未保存更改";
  if (frameworkDraftController.status.value === "saving") return "正在校验并保存框架配置";
  if (frameworkDraftController.status.value === "error") return frameworkDraftController.error.value || "框架配置校验失败";
  const savedAt = frameworkDraftController.lastSavedAt.value;
  return savedAt ? `框架配置已保存 ${savedAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}` : "框架配置已保存";
});
const wizardDraftSaving = computed(() => isLlmWizard.value ? llmDraftSaving.value : frameworkDraftSaving.value);
const wizardDraftStatus = computed(() => isLlmWizard.value ? llmDraftStatus.value : frameworkDraftStatus.value);

const filteredPipelines = computed(() => {
  const normalizedKeyword = keyword.value.trim().toLowerCase();
  const rows = pipelines.value.filter((pipeline) => {
    const tabMatched =
      activeTab.value === "all" ||
      activeTab.value === "mine" ||
      (activeTab.value === "favorite" && Boolean(pipeline.is_favorite));
    const typeMatched = !typeFilter.value || pipeline.task === typeFilter.value;
    const keywordMatched =
      !normalizedKeyword ||
      [pipeline.name, pipeline.task, pipeline.status].filter(Boolean).join(" ").toLowerCase().includes(normalizedKeyword);
    return tabMatched && typeMatched && keywordMatched;
  });

  return [...rows].sort((left, right) => {
    if (sortMode.value === "name") return left.name.localeCompare(right.name, "zh-CN");
    const leftTime = parseTime(left.created_at || left.updated_at);
    const rightTime = parseTime(right.created_at || right.updated_at);
    return sortMode.value === "oldest" ? leftTime - rightTime : rightTime - leftTime;
  });
});

const pagedPipelines = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredPipelines.value.slice(start, start + pageSize.value);
});

const selectableBaseModels = computed(() =>
  baseModels.value.filter((model) => model.task === form.task && (!model.status || model.status === "ready")),
);

const selectableDatasets = computed(() =>
  datasets.value.filter((dataset) => dataset.task === form.task && ["validated", "ready"].includes(dataset.status)),
);

const selectedDataset = computed(() => datasets.value.find((dataset) => dataset.id === form.dataset_id));

const selectedModelName = computed(() => {
  const model = baseModels.value.find((item) => item.id === form.base_model_id);
  return model ? modelLabel(model) : selectedFrameworkModel.value?.display_name || "待选择";
});

const detailPipeline = computed(() => pipelines.value.find((pipeline) => pipeline.id === detailPipelineId.value) ?? null);
const detailJob = computed(() => (detailPipeline.value ? latestJobsByPipeline.value[detailPipeline.value.id] : undefined));
const detailModelName = computed(() => {
  const pipeline = detailPipeline.value;
  if (!pipeline?.base_model_id) return "-";
  const model = baseModels.value.find((item) => item.id === pipeline.base_model_id);
  return model ? modelLabel(model) : pipeline.base_model_id;
});
const detailDatasetName = computed(() => {
  const pipeline = detailPipeline.value;
  if (!pipeline?.dataset_id) return "-";
  return datasets.value.find((dataset) => dataset.id === pipeline.dataset_id)?.name ?? pipeline.dataset_id;
});
const detailRuntime = computed(() => runtimeText(detailJob.value));
const detailParamRows = computed(() => {
  const params = detailPipeline.value?.params_template ?? detailJob.value?.params ?? {};
  return [
    ["热启动步数（WarmUp Steps）", params.warmup_epochs ?? params.warmup_steps ?? "-"],
    ["批大小(Batch Size)", params.batch ?? "-"],
    ["轮次(Epochs)", params.epochs ?? "-"],
    ["评估间隔(Eval Interval) / epoch", params.eval_interval ?? "-"],
    ["学习率(Learning Rate)", params.lr0 ?? "-"],
    ["log打印间隔(Log Interval) / step", params.log_interval ?? "-"],
    ["类别数量(Class Num)", detailClassCount.value],
    ["预训练权重", params.pretrained === false ? "不使用预训练权重" : "官方预训练模型"],
    ["保存间隔(Save Interval) / epoch", params.save_period ?? "-"],
  ].map(([label, value]) => ({ label: String(label), value: String(value) }));
});
const detailClassCount = computed(() => {
  const names = detailPipeline.value?.dataset_id
    ? datasets.value.find((dataset) => dataset.id === detailPipeline.value?.dataset_id)?.class_schema?.names
    : undefined;
  return Array.isArray(names) && names.length > 0 ? names.length : "-";
});
const detailReadyTrainedModels = computed(() => {
  const pipeline = detailPipeline.value;
  if (!pipeline) return [];
  return trainedModels.value.filter((model) => model.pipeline_id === pipeline.id && model.status === "ready");
});
const trainedWeightOptions = computed<WeightOption[]>(() => {
  const seen = new Set<string>();
  const options: WeightOption[] = [];
  detailReadyTrainedModels.value.forEach((model) => {
    const sourceWeight = trainedModelSourceWeight(model);
    if (!sourceWeight || seen.has(sourceWeight)) return;
    seen.add(sourceWeight);
    const deploymentName = trainedModelDeploymentName(model);
    options.push({
      label: deploymentName || sourceWeight,
      value: sourceWeight,
      modelId: model.id,
      modelName: detailModelName.value === "-" ? model.name : detailModelName.value,
      weightName: sourceWeight,
      deploymentName,
    });
  });
  return options.sort((left, right) => weightOrder(left.value) - weightOrder(right.value) || left.value.localeCompare(right.value));
});
const detailInferenceModelOptions = computed<WeightOption[]>(() => {
  return [...trainedWeightOptions.value, { label: "官方/基础权重", value: "base" }];
});
const inferenceEnvironmentOptions = ["cpu", "0", "gpu-node-1", "gpu-node-2"];
const enabledResourcePools = computed(() => resourcePools.value.filter((pool) => pool.enabled));
const onlineGpuNodes = computed(() =>
  computeNodes.value.filter((node) => node.status === "online" && nodeGpuCount(node) > 0),
);
const trainingResourcePools = computed(() =>
  enabledResourcePools.value.filter((pool) => onlineGpuNodes.value.some((node) => node.resource_pool_id === pool.id)),
);
const trainingNodeOptions = computed(() =>
  onlineGpuNodes.value.filter((node) => node.resource_pool_id === trainingEnvironment.poolId),
);
const selectedTrainingNode = computed(() =>
  onlineGpuNodes.value.find((node) => node.id === trainingEnvironment.nodeId),
);
const selectedTrainingNodeGpuCount = computed(() => Math.max(1, selectedTrainingNode.value ? nodeGpuCount(selectedTrainingNode.value) : 1));
const trainingEnvironmentLabel = computed(() => {
  if (trainingEnvironment.mode === "local") return "本机 CPU";
  const node = selectedTrainingNode.value;
  return node ? `${node.name} · ${nodeGpuSummary(node)} × ${trainingEnvironment.requestedGpus}` : "远程 GPU（待选择）";
});
const deploymentNodeOptions = computed(() =>
  computeNodes.value.filter((node) => node.status === "online" && node.resource_pool_id === deployForm.poolId),
);
const selectedDeploymentNode = computed(() => computeNodes.value.find((node) => node.id === deployForm.nodeId));
const officialDeploymentWeightOptions = computed<WeightOption[]>(() => {
  const pipeline = detailPipeline.value;
  if (!pipeline) return [];
  return baseModels.value
    .filter(
      (model) =>
        model.family.toLowerCase() === "yolo26" &&
        model.task === pipeline.task &&
        model.status === "ready" &&
        Boolean(model.local_uri && model.checksum),
    )
    .map((model) => ({
      label: modelLabel(model),
      value: `official:${model.id}`,
      baseModelId: model.id,
      modelName: modelLabel(model),
      weightName: model.filename,
    }));
});
const deployWeightOptions = computed(() =>
  deployForm.weightSource === "pipeline" ? trainedWeightOptions.value : officialDeploymentWeightOptions.value,
);
const selectedDeployWeightOption = computed(() =>
  deployWeightOptions.value.find((option) => option.value === deployForm.weight),
);
const canDeploy = computed(() =>
  Boolean(
    deployForm.serviceName.trim() &&
      selectedDeployWeightOption.value &&
      deployForm.nodeId &&
      deployForm.instanceName.trim(),
  ),
);
const detailWeightOptions = computed(() => {
  const options = detailInferenceModelOptions.value.filter((option) => option.value !== "base");
  return options.length > 0 ? options : [{ label: "best.pt", value: "best.pt" }];
});
const evaluationDatasetOptions = computed(() => {
  return evaluationDatasetsByStatus(evaluationDatasetTab.value);
});
const evaluationDialogDatasetOptions = computed(() => {
  if (evaluationDatasetSourceTab.value === "sample") return [];
  return evaluationDatasetOptions.value;
});
const selectedEvaluationDataset = computed(() => datasets.value.find((dataset) => dataset.id === evaluationForm.customDatasetId));
const evaluationDatasetDialogTitle = computed(() => (evaluationDatasetTab.value === "validated" ? "已校验数据集" : "未校验数据集"));
const validatedEvaluationDatasetCount = computed(() => evaluationDatasetsByStatus("validated").length);
const unvalidatedEvaluationDatasetCount = computed(() => evaluationDatasetsByStatus("unvalidated").length);
const evaluationRows = computed<EvaluationRow[]>(() => {
  const rows =
    trainedWeightOptions.value.length > 0
      ? trainedWeightOptions.value
      : [
          { label: "best.pt", value: "best.pt" },
          { label: "last.pt", value: "last.pt" },
        ];
  return rows.map((row) => ({
    weight: row.label,
    sourceWeight: row.value,
    score: evaluationScoreForWeight(row.value),
    modelId: row.modelId,
    deploymentName: row.deploymentName,
  }));
});
const selectedEvaluationHistory = computed(
  () =>
    evaluationHistory.value.find((record) => record.id === selectedEvaluationHistoryId.value) ??
    evaluationHistory.value[0],
);
const selectedEvaluationMetricRows = computed(() => {
  const metrics = selectedEvaluationHistory.value?.metrics ?? {};
  const preferred: Array<[string, string]> = [
    ["precision", "metrics/precision(B)"],
    ["recall", "metrics/recall(B)"],
    ["mAP50", "metrics/mAP50(B)"],
    ["mAP50-95", "metrics/mAP50-95(B)"],
    ["fitness", "fitness"],
  ];
  const preferredKeys = new Set(preferred.map(([, key]) => key));
  return [
    ...preferred
      .filter(([, key]) => Object.prototype.hasOwnProperty.call(metrics, key))
      .map(([label, key]) => ({ label, value: formatMetricValue(metrics[key]) })),
    ...Object.entries(metrics)
      .filter(([key]) => !preferredKeys.has(key))
      .map(([key, value]) => ({ label: key, value: formatMetricValue(value) })),
  ];
});

const trainCount = computed(() => splitSummary.value.train.count);
const valCount = computed(() => splitSummary.value.val.count);
const splitSummary = computed(() => {
  const distribution = analysisResult.value?.split_distribution ?? {};
  const total = Math.max(analysisResult.value?.sample_count ?? selectedDataset.value?.sample_count ?? 0, 0);
  return {
    train: splitStats(distribution.train ?? Math.round((total * form.train_ratio) / 100), total),
    val: splitStats(distribution.val ?? Math.round((total * form.val_ratio) / 100), total),
    test: splitStats(distribution.test ?? Math.round((total * form.test_ratio) / 100), total),
  };
});

const classDistributionItems = computed(() => {
  const distribution = analysisResult.value?.class_distribution ?? classDistributionFromSchema();
  const max = Math.max(...Object.values(distribution), 0);
  return Object.entries(distribution)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => ({
      name,
      count,
      percent: max > 0 ? Math.max(6, Math.round((count / max) * 100)) : 0,
    }));
});

const classChartItems = computed(() => {
  const distribution = analysisResult.value?.class_distribution ?? classDistributionFromSchema();
  const splitDistribution = analysisResult.value?.class_distribution_by_split ?? {};
  const names = Array.from(
    new Set([
      ...Object.keys(distribution),
      ...Object.keys(splitDistribution.train ?? {}),
      ...Object.keys(splitDistribution.val ?? {}),
    ]),
  ).sort((left, right) => {
    const leftTotal = distribution[left] ?? 0;
    const rightTotal = distribution[right] ?? 0;
    return rightTotal - leftTotal || left.localeCompare(right, "zh-CN");
  });
  const trainRatio = splitSummary.value.train.percent / 100;
  const valRatio = splitSummary.value.val.percent / 100;
  return names.map((name) => {
    const total = distribution[name] ?? 0;
    return {
      name,
      train: splitDistribution.train?.[name] ?? Math.round(total * trainRatio),
      val: splitDistribution.val?.[name] ?? Math.round(total * valRatio),
    };
  });
});

const classChartMax = computed(() =>
  Math.max(1, ...classChartItems.value.flatMap((item) => [item.train, item.val])),
);

const chartTicks = computed(() => {
  const max = classChartMax.value;
  const step = Math.max(1, Math.ceil(max / 4));
  const top = step * 4;
  return [top, step * 3, step * 2, step, 0];
});

const visibleSamples = computed(() => (processingTab.value === "classes" ? [] : samplesBySplit.value[processingTab.value] ?? []));
const activePreviewSample = computed(() => {
  const samples = visibleSamples.value;
  return samples.find((sample) => sample.id === activeSampleId.value) ?? samples[0];
});

watch([filteredPipelines, pageSize], () => {
  const maxPage = Math.max(1, Math.ceil(filteredPipelines.value.length / pageSize.value));
  if (currentPage.value > maxPage) currentPage.value = maxPage;
});

watch(activeStep, (step) => {
  if (step === 3 && resourcePools.value.length === 0 && !edgeResourcesLoading.value) void loadEdgeResources();
  if (viewMode.value === "wizard" && wizardPipelineId.value) void syncWizardRoute();
});

watch(
  () => evaluationForm.dataset,
  (datasetType) => {
    if (datasetType !== "custom") {
      evaluationForm.customDatasetId = "";
      pendingEvaluationDatasetId.value = "";
    }
  },
);

onMounted(() => {
  void (async () => {
    await loadWorkspace();
    await restoreWizardFromRoute();
  })();
  refreshTimer = window.setInterval(() => {
    if (pipelines.value.some((pipeline) => pipeline.status === "running")) {
      void loadWorkspace({ silent: true });
    }
  }, 5000);
});

onUnmounted(() => {
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer);
});

async function loadWorkspace(options: { silent?: boolean } = {}) {
  if (!options.silent) loading.value = true;
  errorMessage.value = "";
  const [pipelineResult, baseResult, trainedResult, datasetResult, jobResult] = await Promise.allSettled([
    api.listPipelines(),
    api.listBaseModels(),
    api.listTrainedModels({ limit: 200 }),
    api.listDatasets(),
    api.listTrainingJobs({ limit: 200 }),
  ]);
  pipelines.value = unwrapItems<TrainingPipelineRecord>(pipelineResult, "产线");
  baseModels.value = unwrapItems<BaseModelRecord>(baseResult, "基础模型");
  datasets.value = unwrapItems<DatasetRecord>(datasetResult, "数据集");
  latestJobsByPipeline.value = latestJobsByPipelineId(unwrapItems<TrainingJobRecord>(jobResult, "训练任务"));
  trainedModels.value = unwrapItems<TrainedModelRecord>(trainedResult, "训练权重");
  if (!options.silent) loading.value = false;
}

function openCreateDialog() {
  createForm.name = nextPipelineName();
  createForm.localTask = "detect";
  createForm.localScale = "n";
  frameworkParameters.value = {};
  frameworkAdvancedYaml.value = "";
  frameworkSelection.value = {
    taskKind: "object_detection",
    framework: "",
    adapterKey: "",
    adapterVersion: "",
    modelKey: "",
  };
  localModelFile.value = null;
  if (modelFileInput.value) modelFileInput.value.value = "";
  createDialogVisible.value = true;
  createTab.value = "zero";
  void loadFrameworkCapabilities("object_detection");
}

function nextPipelineName() {
  const base = "新建产线";
  const names = new Set(pipelines.value.map((pipeline) => pipeline.name.trim()));
  if (!names.has(base)) return base;
  let suffix = 2;
  while (names.has(`${base}${suffix}`)) suffix += 1;
  return `${base}${suffix}`;
}

function selectScenario(key: string) {
  createForm.scenarioKey = key;
  const scenario = scenarios.find((item) => item.key === key);
  if (scenario && implementedZeroCodeTasks.has(scenario.task)) {
    void loadFrameworkCapabilities(taskKindForPipelineTask(scenario.task));
  } else {
    frameworkCatalogRequestSequence += 1;
    frameworkCatalog.value = null;
    frameworkCatalogError.value = "";
  }
}

let frameworkCatalogRequestSequence = 0;

async function loadFrameworkCapabilities(taskKind: string) {
  const requestId = ++frameworkCatalogRequestSequence;
  frameworkCatalogError.value = "";
  try {
    const catalog = await api.getFrameworkCapabilities(taskKind);
    if (requestId !== frameworkCatalogRequestSequence) return;
    frameworkCatalog.value = catalog ?? { task_kind: taskKind, adapters: [] };
    const current = frameworkSelection.value;
    const currentAdapter = compatibleFrameworks(frameworkCatalog.value, taskKind)
      .find((adapter) => adapter.framework === current.framework);
    const currentModel = taskForFramework(currentAdapter, taskKind)?.models
      .find((model) => model.model_key === current.modelKey);
    if (currentAdapter && currentModel) {
      frameworkSelection.value = {
        taskKind,
        framework: currentAdapter.framework,
        adapterKey: currentAdapter.adapter_key,
        adapterVersion: currentAdapter.adapter_version,
        modelKey: currentModel.model_key,
      };
    } else {
      const selection = defaultFrameworkModelSelection(frameworkCatalog.value, taskKind);
      const fallbackAdapter = compatibleFrameworks(frameworkCatalog.value, taskKind)[0];
      const fallbackModel = fallbackAdapter && taskForFramework(fallbackAdapter, taskKind)?.models[0];
      if (selection) frameworkSelection.value = selection;
      else if (fallbackAdapter && fallbackModel) {
        frameworkSelection.value = {
          taskKind,
          framework: fallbackAdapter.framework,
          adapterKey: fallbackAdapter.adapter_key,
          adapterVersion: fallbackAdapter.adapter_version,
          modelKey: fallbackModel.model_key,
        };
      }
    }
  } catch (error) {
    if (requestId !== frameworkCatalogRequestSequence) return;
    frameworkCatalog.value = null;
    frameworkCatalogError.value = getErrorMessage(error, "Unable to load framework capabilities");
  }
}

function setFrameworkSelection(selection: FrameworkModelSelection) {
  frameworkSelection.value = selection;
  const adapter = compatibleFrameworks(frameworkCatalog.value, selection.taskKind)
    .find((item) => item.framework === selection.framework);
  const parameters = taskForFramework(adapter, selection.taskKind)?.parameters ?? [];
  frameworkParameters.value = Object.fromEntries(
    parameters.filter((parameter) => parameter.default !== null && parameter.default !== undefined)
      .map((parameter) => [parameter.name, parameter.default]),
  );
  frameworkAdvancedYaml.value = "";
  applyFrameworkParameterDefaults(frameworkParameters.value);
  configParams.value = {};
  configMode.value = false;
  syncFrameworkModelSelection();
  if (selection.taskKind === "llm_sft") {
    const model = taskForFramework(adapter, selection.taskKind)?.models
      .find((item) => item.model_key === selection.modelKey);
    if (model) llmForm.value.modelId = model.runtime_id || model.model_key;
  }
}

function setFrameworkModel(modelKey: string) {
  if (!modelKey) return;
  frameworkSelection.value = { ...frameworkSelection.value, modelKey };
  syncFrameworkModelSelection();
}

function syncFrameworkModelSelection() {
  const model = selectedFrameworkModel.value;
  form.base_model_id = "";
  if (!model) return;
  if (model.variant) form.scale = model.variant;
  if (!requiresBaseModel.value) return;
  const runtimeFilename = String(model.runtime_id || "").split(/[\\/]/).pop()?.toLowerCase();
  const matched = selectableBaseModels.value.find((baseModel) => {
    const filename = String(baseModel.filename || "").toLowerCase();
    return (runtimeFilename && filename === runtimeFilename)
      || (model.variant && baseModel.scale.toLowerCase() === model.variant.toLowerCase()
        && (!model.family || baseModel.family.toLowerCase() === model.family.toLowerCase()));
  });
  form.base_model_id = matched?.id || "";
}

function frameworkPipelineFields() {
  const adapter = compatibleFrameworks(frameworkCatalog.value, frameworkSelection.value.taskKind)
    .find((item) => item.framework === frameworkSelection.value.framework);
  const model = taskForFramework(adapter, frameworkSelection.value.taskKind)?.models
    .find((item) => item.model_key === frameworkSelection.value.modelKey);
  if (!adapter || !model) return {};
  const task = pipelineTaskForTaskKind(frameworkSelection.value.taskKind);
  return {
    engine: legacyEngineForFramework(adapter.framework),
    task,
    scale: model.variant || (task === "llm" ? "llm" : form.scale),
    framework: adapter.framework,
    adapter_key: adapter.adapter_key,
    adapter_version: adapter.adapter_version,
    task_kind: frameworkSelection.value.taskKind,
    model_family: model.family || model.model_key,
    recipe: { model: { key: model.model_key } },
    params_template: frameworkTrainingParams(),
  };
}

function fixedLlmPipelineFields() {
  const configured = frameworkPipelineFields();
  if (Object.keys(configured).length) return configured;
  return {
    engine: "llamafactory" as const,
    task: "llm",
    scale: "0.6b",
    framework: "llamafactory",
    adapter_key: "llamafactory.llm_sft.v1",
    adapter_version: "1.0.0",
    task_kind: "llm_sft",
    model_family: "qwen3",
    recipe: { model: { key: "qwen3-0.6b" } },
    params_template: {},
  };
}

function frameworkParameterPayload() {
  return frameworkTrainingParams();
}

function selectLocalModelFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  if (file && !file.name.toLowerCase().endsWith(".pt")) {
    localModelFile.value = null;
    input.value = "";
    ElMessage.warning("请选择 Ultralytics / PyTorch .pt 权重文件");
    return;
  }
  localModelFile.value = file;
}

async function startWizard() {
  const name = createForm.name.trim();
  if (!name) {
    ElMessage.warning("请输入产线名称");
    return;
  }
  if (createTab.value === "local" && !localModelFile.value) {
    ElMessage.warning("请上传本地 .pt 模型权重");
    return;
  }
  const scenarioTask = selectedScenario.value.task;
  const usesRegisteredFramework = createTab.value === "zero" && implementedZeroCodeTasks.has(scenarioTask);
  if (usesRegisteredFramework) {
    const expectedTaskKind = taskKindForPipelineTask(selectedScenario.value.task);
    if (frameworkCatalog.value?.task_kind !== expectedTaskKind) {
      await loadFrameworkCapabilities(expectedTaskKind);
    }
  }
  const task = createTab.value === "local"
    ? createForm.localTask
    : scenarioTask;
  const scale = createTab.value === "local" ? createForm.localScale : task === "llm" ? "llm" : task === "detect" ? "n" : "pending";
  form.name = name;
  form.task = task;
  form.scale = scale;
  form.base_model_id = "";
  form.dataset_id = "";
  analysisResult.value = null;
  samplesBySplit.value = { train: [], val: [], test: [] };
  activeSampleId.value = "";
  processingTab.value = "train";
  configParams.value = {};
  if (task === "llm") {
    llmForm.value = createDefaultLlmTrainingForm(name);
    llmForm.value.modelId = selectedFrameworkModel.value?.runtime_id || selectedFrameworkModel.value?.model_key || "";
    trainingEnvironment.mode = "remote";
  } else {
    ensureWizardDefaults();
  }
  submitting.value = true;
  try {
    let uploadedModel: BaseModelRecord | null = null;
    if (createTab.value === "local" && localModelFile.value) {
      uploadedModel = await api.uploadBaseModel(localModelFile.value, { task, scale });
      baseModels.value = [uploadedModel, ...baseModels.value.filter((model) => model.id !== uploadedModel?.id)];
      form.base_model_id = uploadedModel.id;
      createForm.scenarioKey = scenarioKeyForTask(task);
    }
    const frameworkFields = createTab.value === "zero"
      ? usesRegisteredFramework
        ? task === "llm" ? fixedLlmPipelineFields() : frameworkPipelineFields()
        : pendingCapabilityPipelineFields(task)
      : {};
    if (usesRegisteredFramework && !Object.keys(frameworkFields).length) {
      throw new Error("请选择可用的训练框架和模型");
    }
    const pipeline = await api.createPipeline({
      name: form.name,
      ...(createTab.value === "local"
        ? { engine: form.task === "llm" ? "llamafactory" : "yolo26", task: form.task, scale: form.scale }
        : frameworkFields),
      ...(uploadedModel ? { base_model_id: uploadedModel.id } : {}),
    });
    wizardPipelineId.value = pipeline.id;
    pipelines.value = [pipeline, ...pipelines.value.filter((item) => item.id !== pipeline.id)];
    activeStep.value = 0;
    createDialogVisible.value = false;
    viewMode.value = "wizard";
    await syncWizardRoute();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "创建产线失败"));
  } finally {
    submitting.value = false;
  }
}

async function openExistingPipelineWizard(pipeline: TrainingPipelineRecord) {
  wizardPipelineId.value = pipeline.id;
  createForm.scenarioKey = scenarioKeyForTask(pipeline.task);
  form.name = pipeline.name;
  form.task = pipeline.task || "detect";
  form.scale = pipeline.scale || "n";
  form.base_model_id = pipeline.base_model_id || "";
  form.dataset_id = pipeline.dataset_id || "";
  frameworkSelection.value = {
    taskKind: pipeline.task_kind || taskKindForPipelineTask(pipeline.task),
    framework: pipeline.framework || (pipeline.engine === "yolo26" ? "ultralytics" : pipeline.engine) || "ultralytics",
    adapterKey: pipeline.adapter_key || "",
    adapterVersion: pipeline.adapter_version || "",
    modelKey: String((pipeline.recipe?.model as Record<string, unknown> | undefined)?.key || ""),
  };
  frameworkParameters.value = pipeline.params_template ?? {};
  frameworkAdvancedYaml.value = "";
  void loadFrameworkCapabilities(frameworkSelection.value.taskKind);
  if (pipeline.task === "llm") {
    llmForm.value = llmFormFromPipeline(pipeline);
    trainingEnvironment.mode = "remote";
  } else {
    applyPipelineParams(pipeline);
    configParams.value = pipeline.framework === "paddlex"
      ? { ...(pipeline.params_template ?? {}) }
      : normalizeConfigParams(pipeline.params_template ?? {});
  }
  analysisResult.value = null;
  samplesBySplit.value = { train: [], val: [], test: [] };
  activeSampleId.value = "";
  processingTab.value = "train";
  if (pipeline.task !== "llm") ensureWizardDefaults();
  activeStep.value = 0;
  viewMode.value = "wizard";
  await syncWizardRoute();
  if (form.dataset_id && pipeline.task !== "llm") await loadWizardProcessingData();
}

async function cloneWithFrameworkSelection() {
  const source = activeWizardPipeline.value;
  if (!source) return;
  await clonePipelineForEditing(source);
}

async function clonePipelineForEditing(source: TrainingPipelineRecord) {
  try {
    const clone = await api.clonePipeline(source.id, {
      name: `${source.name} copy`,
    });
    pipelines.value = [clone, ...pipelines.value];
    await openExistingPipelineWizard(clone);
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "Unable to clone pipeline"));
  }
}

function openPipelineCard(pipeline: TrainingPipelineRecord) {
  if (isConfiguredPipeline(pipeline)) {
    void openExistingPipelineWizard(pipeline);
    return;
  }
  openPipelineDetail(pipeline);
}

function openPipelineDetail(pipeline: TrainingPipelineRecord) {
  detailPipelineId.value = pipeline.id;
  detailTab.value = "basic";
  deployMode.value = "online";
  deployForm.serviceName = `${pipeline.name}-service`;
  selectDeploymentWeightSource(trainedWeightOptions.value.length > 0 ? "pipeline" : "official");
  deployForm.environment = "";
  deployForm.instanceName = `${pipeline.name}-01`;
  deployForm.poolId = "";
  deployForm.nodeId = "";
  deployForm.optimizationMode = "auto";
  deployForm.format = "engine";
  deployForm.precision = "fp16";
  deployForm.inputSize = 640;
  deployForm.calibrationDatasetUri = "";
  evaluationForm.dataset = "val";
  evaluationForm.customDatasetId = "";
  evaluationForm.weight = "";
  evaluationForm.environment = "cpu";
  latestEvaluation.value = null;
  evaluationHistory.value = [];
  selectedEvaluationHistoryId.value = "";
  evaluationHistoryLoading.value = false;
  detailLogText.value = "";
  viewMode.value = "detail";
}

function openDetailTab(tab: DetailTab) {
  detailTab.value = tab;
  if (tab === "logs") void loadDetailLog();
  if (tab === "evaluate") void loadEvaluationHistory();
  if (tab === "deploy") void loadEdgeResources();
}

function openTrainingVisualization() {
  const jobId = detailJob.value?.id;
  void router.push({
    path: "/training-visualization",
    ...(jobId ? { query: { job: jobId } } : {}),
  });
}

function setDeployMode(mode: "online" | "offline") {
  deployMode.value = mode;
  if (mode === "offline") {
    deployForm.weight = detailWeightOptions.value[0]?.value ?? "";
  } else {
    selectDeploymentWeightSource(deployForm.weightSource);
  }
}

function selectDeploymentWeightSource(source: "pipeline" | "official") {
  deployForm.weightSource = source;
  const options = source === "pipeline" ? trainedWeightOptions.value : officialDeploymentWeightOptions.value;
  deployForm.weight = options[0]?.value ?? "";
}

function exportOfflineModel() {
  const job = detailJob.value;
  if (!job?.id || !deployForm.weight) {
    ElMessage.warning("暂无可导出的模型权重");
    return;
  }
  window.open(
    api.trainingJobArtifactDownloadUrl(job.id, "weight", deployForm.weight),
    "_blank",
    "noopener,noreferrer",
  );
}

async function startDeployment() {
  const pipeline = detailPipeline.value;
  const node = selectedDeploymentNode.value;
  const weight = selectedDeployWeightOption.value;
  if (!pipeline || !node || !weight || !canDeploy.value) {
    ElMessage.warning("请完整填写部署配置");
    return;
  }
  const gpuUuids = deploymentGpuUuids(node);
  deploying.value = true;
  try {
    await api.createService({
      name: deployForm.serviceName.trim(),
      pipeline_id: pipeline.id,
      ...(weight.modelId ? { trained_model_id: weight.modelId } : {}),
      ...(weight.baseModelId ? { base_model_id: weight.baseModelId } : {}),
      model_name: weight.modelName ?? detailModelName.value,
      model_weight: weight.weightName ?? weight.label,
      environment: node.id,
      instance_name: deployForm.instanceName.trim(),
      resource_summary: `${node.name} · ${nodeResourceSummary(node)}`,
      node_id: node.id,
      format: deployForm.optimizationMode === "auto" ? "auto" : deployForm.format,
      precision: deployForm.optimizationMode === "auto" ? "auto" : deployForm.precision,
      input_shape: [1, 3, deployForm.inputSize, deployForm.inputSize],
      gpu_uuids: gpuUuids,
      ...(deployForm.precision === "int8" && deployForm.calibrationDatasetUri.trim()
        ? { calibration_dataset_uri: deployForm.calibrationDatasetUri.trim() }
        : {}),
      config: {
        task: pipeline.task,
        pipeline_name: pipeline.name,
        resource_pool_id: deployForm.poolId,
        optimization_mode: deployForm.optimizationMode,
      },
    });
    ElMessage.success("部署任务已提交");
    await router.push("/services");
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "服务部署失败"));
  } finally {
    deploying.value = false;
  }
}

async function loadEdgeResources() {
  edgeResourcesLoading.value = true;
  edgeResourceError.value = "";
  try {
    const [poolResult, nodeResult] = await Promise.all([api.listResourcePools(), api.listNodes()]);
    resourcePools.value = poolResult.items;
    computeNodes.value = nodeResult.items;
    const firstPool = enabledResourcePools.value.find((pool) =>
      computeNodes.value.some((node) => node.resource_pool_id === pool.id && node.status === "online"),
    );
    if (!deployForm.poolId && firstPool) selectDeploymentPool(firstPool.id);
    if (!trainingEnvironment.poolId) {
      const firstTrainingPool = trainingResourcePools.value[0];
      if (firstTrainingPool) selectTrainingPool(firstTrainingPool.id);
    }
  } catch (error) {
    edgeResourceError.value = getErrorMessage(error, "边缘资源加载失败");
  } finally {
    edgeResourcesLoading.value = false;
  }
}

function selectTrainingTarget(mode: TrainingEnvironmentMode) {
  trainingEnvironment.mode = mode;
  form.device = mode === "local" ? "cpu" : "0";
  if (mode === "remote") {
    if (resourcePools.value.length === 0 && !edgeResourcesLoading.value) void loadEdgeResources();
    const pool = trainingResourcePools.value.find((item) => item.id === trainingEnvironment.poolId) ?? trainingResourcePools.value[0];
    if (pool) selectTrainingPool(pool.id);
  }
}

function selectTrainingPool(poolId: string) {
  trainingEnvironment.poolId = poolId;
  const firstNode = onlineGpuNodes.value.find((node) => node.resource_pool_id === poolId);
  selectTrainingNode(firstNode?.id ?? "");
}

function selectTrainingNode(nodeId: string) {
  trainingEnvironment.nodeId = nodeId;
  const node = onlineGpuNodes.value.find((item) => item.id === nodeId);
  trainingEnvironment.requestedGpus = Math.min(Math.max(1, trainingEnvironment.requestedGpus), Math.max(1, node ? nodeGpuCount(node) : 1));
}

function selectDeploymentPool(poolId: string) {
  deployForm.poolId = poolId;
  const firstNode = computeNodes.value.find((node) => node.resource_pool_id === poolId && node.status === "online");
  deployForm.nodeId = firstNode?.id ?? "";
  deployForm.environment = firstNode?.id ?? "";
}

function selectDeploymentNode(nodeId: string) {
  deployForm.nodeId = nodeId;
  deployForm.environment = nodeId;
}

function deploymentGpuUuids(node: ComputeNodeRecord) {
  const direct = node.capabilities.gpu_uuids;
  if (Array.isArray(direct)) return direct.filter((value): value is string => typeof value === "string");
  const gpus = node.resources.gpus;
  const candidates = Array.isArray(gpus) ? gpus : inventoryGpus(node);
  return candidates
    .map((gpu) => (gpu && typeof gpu === "object" ? (gpu as Record<string, unknown>).uuid : undefined))
    .filter((value): value is string => typeof value === "string");
}

function nodeResourceSummary(node: ComputeNodeRecord) {
  const gpu = nodeGpuSummary(node);
  const free = Number(node.resources.gpu_memory_free_mb ?? 0);
  const total = nodeGpuMemoryTotalMib(node);
  return total > 0 ? `${gpu}（${free > 0 ? `${free} / ` : ""}${total} MiB）` : gpu;
}

function nodeGpuCount(node: ComputeNodeRecord) {
  const direct = Number(node.resources.gpu_count ?? 0);
  if (direct > 0) return direct;
  const resourceGpus = node.resources.gpus;
  if (Array.isArray(resourceGpus)) return resourceGpus.length;
  return inventoryGpus(node).length;
}

function nodeGpuSummary(node: ComputeNodeRecord) {
  const direct = node.resources.gpu_name ?? node.resources.gpu_model;
  if (typeof direct === "string" && direct.trim()) return direct.trim();
  const capabilityModels = node.capabilities.gpu_models;
  if (Array.isArray(capabilityModels) && capabilityModels.length > 0) return capabilityModels.map(String).join(" / ");
  const names = inventoryGpus(node).map((gpu) => gpu.name).filter((name): name is string => typeof name === "string" && Boolean(name));
  return names.length > 0 ? Array.from(new Set(names)).join(" / ") : node.platform_kind;
}

function nodeGpuMemoryTotalMib(node: ComputeNodeRecord) {
  const mib = Number(node.resources.gpu_memory_total_mib ?? node.resources.gpu_memory_total_mb ?? 0);
  if (mib > 0) return mib;
  const bytes = Number(node.resources.gpu_memory_total_bytes ?? 0);
  if (bytes > 0) return Math.round(bytes / 1024 / 1024);
  return inventoryGpus(node).reduce((total, gpu) => total + Number(gpu.memory_total_mib ?? 0), 0);
}

function inventoryGpus(node: ComputeNodeRecord): Array<Record<string, unknown>> {
  const snapshot = node.fingerprint.inventory_snapshot;
  if (!snapshot || typeof snapshot !== "object") return [];
  const gpus = (snapshot as Record<string, unknown>).gpus;
  return Array.isArray(gpus) ? gpus.filter((gpu): gpu is Record<string, unknown> => Boolean(gpu) && typeof gpu === "object") : [];
}

function platformLabel(kind: string) {
  if (kind === "x86_nvidia") return "x86 NVIDIA GPU";
  if (kind === "jetson") return "NVIDIA Jetson";
  return kind;
}

function poolCompatibility(pool: ResourcePoolRecord) {
  const policy = pool.compatibility_policy;
  const values = [
    policy.cuda_major ? `CUDA ${policy.cuda_major}` : "",
    policy.tensorrt_major ? `TensorRT ${policy.tensorrt_major}` : "",
    policy.compute_capability ? `SM ${policy.compute_capability}` : "",
  ].filter(Boolean);
  return values.join(" · ") || "兼容资源池";
}

function openEvaluationSubtab(tab: "pipeline" | "history") {
  evaluationTab.value = tab;
  void loadEvaluationHistory();
}

function backToList() {
  viewMode.value = "list";
  activeStep.value = 0;
  detailPipelineId.value = "";
  wizardPipelineId.value = "";
  void router.replace({ path: "/model-space", query: {} });
}

function wizardStepFromQuery(value: unknown) {
  const index = wizardStepKeys.indexOf(String(value) as (typeof wizardStepKeys)[number]);
  return index >= 0 ? index : 0;
}

function syncWizardRoute() {
  if (!wizardPipelineId.value) return Promise.resolve();
  return router.replace({
    path: "/model-space",
    query: {
      pipeline: wizardPipelineId.value,
      step: wizardStepKeys[activeStep.value] ?? wizardStepKeys[0],
    },
  });
}

async function restoreWizardFromRoute() {
  const pipelineId = typeof route.query.pipeline === "string" ? route.query.pipeline : "";
  if (!pipelineId) return;
  const requestedStep = wizardStepFromQuery(route.query.step);
  const pipeline = pipelines.value.find((item) => item.id === pipelineId);
  if (!pipeline || !isConfiguredPipeline(pipeline)) return;
  await openExistingPipelineWizard(pipeline);
  activeStep.value = requestedStep;
  await syncWizardRoute();
}

function setTab(tab: ActiveTab) {
  activeTab.value = tab;
  currentPage.value = 1;
}

function syncScaleFromModel() {
  const selected = baseModels.value.find((model) => model.id === form.base_model_id);
  if (selected) form.scale = selected.scale;
}

async function updateClassCountFromDataset() {
  const names = selectedDataset.value?.class_schema?.names;
  if (Array.isArray(names) && names.length > 0) form.class_num = names.length;
  await loadWizardProcessingData();
}

function rebalanceSplit() {
  form.val_ratio = 100 - form.train_ratio;
  form.test_ratio = 0;
}

async function goToWizardStep(index: number) {
  if (index === activeStep.value) return;
  if (isPendingCapabilityWizard.value) return;
  if (index < activeStep.value) {
    activeStep.value = index;
    return;
  }
  while (activeStep.value < index) {
    const previousStep = activeStep.value;
    await goNext();
    if (activeStep.value === previousStep) return;
  }
}

async function goNext() {
  if (isPendingCapabilityWizard.value) {
    ElMessage.info("该任务训练能力待接入");
    return;
  }
  if (isLlmWizard.value) {
    const validation = llmWizardRef.value?.validateStep(activeStep.value) ?? { valid: false, message: "大模型配置尚未就绪" };
    if (!validation.valid) {
      ElMessage.warning(validation.message);
      return;
    }
    try {
      await llmDraftController.saveNow();
      activeStep.value += 1;
    } catch (error) {
      ElMessage.error(getErrorMessage(error, "大模型草稿保存失败"));
    }
    return;
  }
  if (activeStep.value === 0) {
    ensureWizardDefaults();
    activeStep.value += 1;
    await loadWizardProcessingData();
    return;
  }
  if (activeStep.value === 1) {
    if (requiresBaseModel.value && !form.base_model_id) {
      ElMessage.warning("请选择基础模型");
      return;
    }
    if (!form.dataset_id) {
      ElMessage.warning("请选择数据集");
      return;
    }
    await updateClassCountFromDataset();
  }
  if (activeStep.value === 2 && !frameworkConfigValid.value) {
    ElMessage.warning("请先修正训练参数配置中的错误");
    return;
  }
  activeStep.value += 1;
}

function ensureWizardDefaults() {
  if (requiresBaseModel.value && !form.base_model_id) {
    const firstModel = selectableBaseModels.value[0];
    if (firstModel) {
      form.base_model_id = firstModel.id;
      form.scale = firstModel.scale;
    }
  }
  if (!form.dataset_id) {
    const firstDataset = selectableDatasets.value[0];
    if (firstDataset) form.dataset_id = firstDataset.id;
  }
  const names = selectedDataset.value?.class_schema?.names;
  if (Array.isArray(names) && names.length > 0) form.class_num = names.length;
}

async function loadWizardProcessingData() {
  if (!form.dataset_id) {
    analysisResult.value = null;
    samplesBySplit.value = { train: [], val: [], test: [] };
    activeSampleId.value = "";
    return;
  }
  processingLoading.value = true;
  try {
    const [analysisTask, trainSamples, valSamples, testSamples] = await Promise.all([
      api.analyzeDataset(form.dataset_id),
      api.listDatasetSamples(form.dataset_id, { split: "train", limit: 60 }),
      api.listDatasetSamples(form.dataset_id, { split: "val", limit: 60 }),
      api.listDatasetSamples(form.dataset_id, { split: "test", limit: 60 }),
    ]);
    analysisResult.value = readAnalysisResult(analysisTask.payload);
    samplesBySplit.value = {
      train: normalizeRows<DatasetSampleRecord>(trainSamples),
      val: normalizeRows<DatasetSampleRecord>(valSamples),
      test: normalizeRows<DatasetSampleRecord>(testSamples),
    };
    const first = samplesBySplit.value.train[0] ?? samplesBySplit.value.val[0] ?? samplesBySplit.value.test[0];
    activeSampleId.value = first?.id ?? "";
  } catch (error) {
    analysisResult.value = null;
    samplesBySplit.value = { train: [], val: [], test: [] };
    activeSampleId.value = "";
    ElMessage.error(getErrorMessage(error, "数据分析加载失败"));
  } finally {
    processingLoading.value = false;
  }
}

async function submitTraining() {
  if (isLlmWizard.value) {
    await submitLlmTraining();
    return;
  }
  if ((requiresBaseModel.value && !form.base_model_id) || !form.dataset_id) {
    ElMessage.warning("请先选择模型和数据集");
    return;
  }
  if (!frameworkConfigValid.value) {
    ElMessage.warning("请先修正训练参数配置中的错误");
    return;
  }
  if (trainingEnvironment.mode === "remote") {
    if (!trainingEnvironment.poolId || !trainingEnvironment.nodeId) {
      ElMessage.warning("请选择在线 GPU 服务器");
      return;
    }
    if (!trainingEnvironment.imageDigest.trim()) {
      ElMessage.warning("请配置远程训练镜像摘要");
      return;
    }
  }
  submitting.value = true;
  try {
    const defaultEnvironment = { device: trainingEnvironment.mode === "remote" ? "0" : "cpu", workers: 2 };
    const jobPayload = trainingJobPayload(defaultEnvironment);
    const frameworkFields = frameworkPipelineFields();
    const paramsTemplate = frameworkTrainingParams();
    if (wizardPipelineId.value) {
      await api.updatePipeline(wizardPipelineId.value, {
        ...frameworkFields,
        base_model_id: requiresBaseModel.value ? form.base_model_id : null,
        dataset_id: form.dataset_id,
        params_template: paramsTemplate,
        default_environment: defaultEnvironment,
      });
      await api.createTrainingJob(wizardPipelineId.value, jobPayload);
      ElMessage.success("已提交训练");
      await loadWorkspace();
      backToList();
      return;
    }
    const pipeline = await api.createPipeline({
      name: form.name,
      ...frameworkFields,
      base_model_id: requiresBaseModel.value ? form.base_model_id : null,
      dataset_id: form.dataset_id,
      params_template: paramsTemplate,
      default_environment: defaultEnvironment,
    });
    await api.createTrainingJob(pipeline.id, jobPayload);
    ElMessage.success("已提交训练");
    await loadWorkspace();
    backToList();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "提交训练失败"));
  } finally {
    submitting.value = false;
  }
}

async function saveLlmDraft() {
  if (!wizardPipelineId.value) return;
  const defaultEnvironment = {
    device: "remote",
    workers: llmForm.value.dataloaderWorkers,
    ...(llmForm.value.poolId ? { resource_pool_id: llmForm.value.poolId } : {}),
    ...(llmForm.value.nodeId ? { node_id: llmForm.value.nodeId } : {}),
  };
  const updated = await api.updatePipeline(wizardPipelineId.value, {
    ...llmFrameworkPipelineFields(),
    name: llmForm.value.name.trim(),
    engine: "llamafactory",
    task: "llm",
    scale: "llm",
    dataset_id: llmForm.value.datasetId || null,
    params_template: llmPipelineParams(),
    default_environment: defaultEnvironment,
  });
  form.name = llmForm.value.name.trim();
  form.dataset_id = llmForm.value.datasetId;
  replacePipeline(updated);
}

function llmFrameworkPipelineFields() {
  const adapter = selectedFrameworkAdapter.value;
  const modelId = llmForm.value.modelId.trim();
  if (!adapter || !modelId) return fixedLlmPipelineFields();
  const catalogModel = taskForFramework(adapter, "llm_sft")?.models.find(
    (model) => model.model_key === modelId || model.runtime_id === modelId,
  );
  const family = catalogModel?.family || modelId;
  const variant = catalogModel?.variant || "custom";
  return {
    engine: "llamafactory" as const,
    task: "llm",
    scale: variant,
    framework: adapter.framework,
    adapter_key: adapter.adapter_key,
    adapter_version: adapter.adapter_version,
    task_kind: "llm_sft",
    model_family: family,
    recipe: {
      model: {
        key: catalogModel?.model_key || modelId,
        label: catalogModel?.display_name || modelId,
        runtime_id: catalogModel?.runtime_id || modelId,
        family,
        variant,
        source: catalogModel?.source || llmForm.value.modelSource,
        revision: llmForm.value.requestedRevision.trim() || "main",
      },
    },
  };
}

async function saveFrameworkDraft() {
  if (!wizardPipelineId.value) return;
  const updated = await api.updatePipeline(wizardPipelineId.value, {
    ...frameworkPipelineFields(),
  });
  replacePipeline(updated);
}

async function saveLlmDraftFromAction() {
  try {
    await llmDraftController.saveNow();
    ElMessage.success("草稿已保存");
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "大模型草稿保存失败"));
  }
}

async function saveWizardDraftFromAction() {
  if (isLlmWizard.value) {
    await saveLlmDraftFromAction();
    return;
  }
  try {
    await frameworkDraftController.saveNow();
    ElMessage.success("框架配置已校验并保存");
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "框架配置保存失败"));
  }
}

async function submitLlmTraining() {
  const validation = llmWizardRef.value?.validateStep(3) ?? { valid: false, message: "大模型配置尚未就绪" };
  if (!validation.valid) {
    ElMessage.warning(validation.message);
    return;
  }
  submitting.value = true;
  try {
    await llmDraftController.saveNow();
    if (!wizardPipelineId.value) throw new Error("大模型产线尚未创建");
    await api.createTrainingJob(wizardPipelineId.value, {
      environment: {
        device: "remote",
        workers: llmForm.value.dataloaderWorkers,
        resource_pool_id: llmForm.value.poolId,
        node_id: llmForm.value.nodeId,
      },
      distributed: {
        resource_pool_id: llmForm.value.poolId,
        requested_gpus: 1,
        node_ids: [llmForm.value.nodeId],
      },
    });
    ElMessage.success("大模型训练已提交");
    await loadWorkspace();
    backToList();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "大模型训练提交失败"));
  } finally {
    submitting.value = false;
  }
}

function trainingJobPayload(environment: { device: string; workers: number }) {
  if (trainingEnvironment.mode === "local") return {};
  return {
    environment,
    distributed: {
      resource_pool_id: trainingEnvironment.poolId,
      requested_gpus: trainingEnvironment.requestedGpus,
      node_ids: [trainingEnvironment.nodeId],
      training_image_digest: trainingEnvironment.imageDigest.trim(),
    },
  };
}

async function stopTrainingPipeline(pipeline: TrainingPipelineRecord) {
  const job = latestJobsByPipeline.value[pipeline.id];
  if (!job?.task_id) {
    ElMessage.warning("未找到可停止的训练任务");
    await loadWorkspace();
    return;
  }
  stoppingPipelineId.value = pipeline.id;
  try {
    await api.cancelTask(job.task_id);
    replacePipeline({ ...pipeline, status: "canceled" });
    ElMessage.success("已停止训练");
    await loadWorkspace({ silent: true });
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "停止训练失败"));
  } finally {
    stoppingPipelineId.value = "";
  }
}

async function loadDetailLog() {
  const job = detailJob.value;
  if (!job?.id || !job.log_uri) {
    detailLogText.value = "暂无训练日志。";
    return;
  }
  detailLogLoading.value = true;
  try {
    const response = await fetch(api.trainingJobLogUrl(job.id));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    detailLogText.value = await response.text();
  } catch (error) {
    detailLogText.value = getErrorMessage(error, "训练日志加载失败");
  } finally {
    detailLogLoading.value = false;
  }
}

function downloadDetailLog() {
  const job = detailJob.value;
  if (!job?.id || !job.log_uri) {
    ElMessage.warning("暂无可下载日志");
    return;
  }
  window.open(api.trainingJobLogUrl(job.id), "_blank", "noopener,noreferrer");
}

async function openResultFiles() {
  const job = detailJob.value;
  if (!job?.id) {
    ElMessage.warning("暂无训练结果文件");
    return;
  }
  resultFilesDialogVisible.value = true;
  resultFilesLoading.value = true;
  resultFiles.value = [];
  try {
    const response = await api.listTrainingJobArtifacts(job.id);
    resultFiles.value = response.items;
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "训练结果文件加载失败"));
  } finally {
    resultFilesLoading.value = false;
  }
}

function resultFileDownloadUrl(file: TrainingArtifactRecord) {
  const job = detailJob.value;
  return job ? api.trainingJobArtifactDownloadUrl(job.id, file.kind, file.name) : "#";
}

function formatFileSize(size: number) {
  if (!Number.isFinite(size) || size < 0) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const digits = value >= 10 || Number.isInteger(value) ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unitIndex]}`;
}

async function runDetailInference(request: ExperienceInferenceRequest): Promise<PipelinePredictResponse> {
  const pipeline = detailPipeline.value;
  if (!pipeline) throw new Error("未选择产线");
  return api.predictPipelineImage(pipeline.id, {
    file: request.file,
    model_weight: request.modelWeight,
    environment: request.environment,
  });
}

async function startPipelineEvaluation() {
  const pipeline = detailPipeline.value;
  if (!pipeline) return;
  const weight = evaluationForm.weight || detailWeightOptions.value[0]?.value || "best.pt";
  const environment = evaluationForm.environment.trim() || "cpu";
  if (evaluationForm.dataset === "custom" && !evaluationForm.customDatasetId) {
    ElMessage.warning("请选择自定义测试集");
    return;
  }
  evaluationLoading.value = true;
  try {
    const result = await api.evaluatePipeline(pipeline.id, {
      evaluation_set: evaluationForm.dataset === "custom" ? "custom" : "val",
      dataset_id: evaluationForm.dataset === "custom" ? evaluationForm.customDatasetId : undefined,
      model_weight: weight,
      environment,
    });
    latestEvaluation.value = result;
    evaluationForm.weight = result.model_weight;
    evaluationForm.environment = result.environment;
    upsertEvaluationHistory(result);
    selectedEvaluationHistoryId.value = result.id;
    evaluationTab.value = "history";
    ElMessage.success("评估完成");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "评估失败");
  } finally {
    evaluationLoading.value = false;
  }
}

async function loadEvaluationHistory() {
  const pipeline = detailPipeline.value;
  if (!pipeline || evaluationHistoryLoading.value) return;
  evaluationHistoryLoading.value = true;
  try {
    const result = await api.listPipelineEvaluations(pipeline.id, { limit: 50 });
    evaluationHistory.value = Array.isArray(result.items) ? result.items : [];
    if (
      !selectedEvaluationHistoryId.value ||
      !evaluationHistory.value.some((record) => record.id === selectedEvaluationHistoryId.value)
    ) {
      selectedEvaluationHistoryId.value = evaluationHistory.value[0]?.id ?? "";
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "评估历史加载失败"));
  } finally {
    evaluationHistoryLoading.value = false;
  }
}

function upsertEvaluationHistory(record: PipelineEvaluationResponse) {
  evaluationHistory.value = [record, ...evaluationHistory.value.filter((item) => item.id !== record.id)];
}

function openEvaluationDatasetDialog(tab: "validated" | "unvalidated") {
  evaluationDatasetTab.value = tab;
  evaluationDatasetSourceTab.value = "personal";
  const options = evaluationDatasetsByStatus(tab);
  pendingEvaluationDatasetId.value = options.some((dataset) => dataset.id === evaluationForm.customDatasetId) ? evaluationForm.customDatasetId : "";
  evaluationDatasetDialogVisible.value = true;
}

function confirmEvaluationDatasetSelection() {
  if (!pendingEvaluationDatasetId.value) return;
  evaluationForm.customDatasetId = pendingEvaluationDatasetId.value;
  evaluationDatasetDialogVisible.value = false;
}

function openWeightMarkDialog(row: EvaluationRow) {
  if (!row.modelId) {
    ElMessage.warning("未找到可标记的训练权重");
    return;
  }
  weightMarkModelId.value = row.modelId;
  weightMarkName.value = row.deploymentName || defaultDeploymentName(row.sourceWeight);
  weightMarkDialogVisible.value = true;
}

async function confirmWeightMark() {
  const name = weightMarkName.value.trim();
  if (!/^(?![-_])[\w\u4e00-\u9fff-]+(?<![-_])$/.test(name)) {
    ElMessage.warning("名称最多64个字符，支持中英文、数字、下划线、中划线，且符号不能在首尾");
    return;
  }
  if (!weightMarkModelId.value) return;
  weightMarkLoading.value = true;
  try {
    const updated = await api.markTrainedModelWeight(weightMarkModelId.value, {
      deployment_name: name,
    });
    trainedModels.value = trainedModels.value.map((model) => (model.id === updated.id ? updated : model));
    weightMarkDialogVisible.value = false;
    ElMessage.success("已标记权重");
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "标记权重失败"));
  } finally {
    weightMarkLoading.value = false;
  }
}

function evaluationDatasetsByStatus(tab: "validated" | "unvalidated") {
  const pipeline = detailPipeline.value;
  return datasets.value.filter((dataset) => {
    if (pipeline && dataset.task !== pipeline.task) return false;
    return tab === "validated" ? dataset.status === "validated" : dataset.status !== "validated";
  });
}

function datasetBadgeText(name: string) {
  const trimmed = name.trim();
  if (!trimmed) return "数";
  return trimmed[0].toUpperCase();
}

function trainingParams() {
  return removeEmptyParams({
    ...configParams.value,
    epochs: Number(form.epochs),
    batch: Number(form.batch),
    lr0: Number(form.lr0),
    pretrained: form.pretrained === "false" ? false : true,
    resume: Boolean(form.resume_weight),
    warmup_epochs: Number(form.warmup_epochs),
    save_period: Number(form.save_period),
  });
}

function paddlexTrainingParams() {
  return removeEmptyParams({
    ...configParams.value,
    epochs: Number(form.epochs),
    batch_size: Number(form.batch),
    learning_rate: Number(form.lr0),
    image_size: Number(form.image_size),
    workers: Number(form.workers),
    amp: Boolean(form.amp),
    resume: Boolean(form.resume_weight),
  });
}

function frameworkTrainingParams() {
  return { ...frameworkParameters.value };
}

function applyFrameworkParameterDefaults(params: Record<string, unknown>) {
  if (isPaddleXFramework.value) {
    form.epochs = numberParam(params.epochs, 100);
    form.batch = numberParam(params.batch_size, 8);
    form.lr0 = stringParam(params.learning_rate, "0.001");
    form.image_size = numberParam(params.image_size, 640);
    form.workers = numberParam(params.workers, 4);
    form.amp = params.amp !== false;
    return;
  }
  form.epochs = numberParam(params.epochs, 100);
  form.batch = numberParam(params.batch, 16);
  form.lr0 = stringParam(params.lr0, "0.01");
}

function llmPipelineParams() {
  const modelId = llmForm.value.modelId.trim();
  return {
    model_source: llmForm.value.modelSource,
    ...(modelId
      ? {
          model_id: modelId,
          model_revision: llmForm.value.requestedRevision.trim() || "main",
          resolved_revision: llmForm.value.resolvedRevision || undefined,
        }
      : {}),
    ...toLlamaFactoryConfig(llmForm.value),
  };
}

function handleLlmDatasetUploaded(dataset: DatasetRecord) {
  const index = datasets.value.findIndex((item) => item.id === dataset.id);
  if (index >= 0) datasets.value[index] = dataset;
  else datasets.value.push(dataset);
}

function llmFormFromPipeline(pipeline: TrainingPipelineRecord): LlmTrainingForm {
  const params = pipeline.params_template ?? {};
  const environment = pipeline.default_environment ?? {};
  const defaults = createDefaultLlmTrainingForm(pipeline.name);
  const numberValue = (key: string, fallback: number) => {
    const value = Number(params[key]);
    return Number.isFinite(value) ? value : fallback;
  };
  return {
    ...defaults,
    name: pipeline.name,
    modelSource: params.model_source === "modelscope" ? "modelscope" : "huggingface",
    modelId: typeof params.model_id === "string" ? params.model_id : "",
    requestedRevision: typeof params.model_revision === "string" ? params.model_revision : "main",
    resolvedRevision: typeof params.resolved_revision === "string" ? params.resolved_revision : "",
    template: typeof params.template === "string" ? params.template : "auto",
    trustRemoteCode: Boolean(params.trust_remote_code),
    datasetId: pipeline.dataset_id || "",
    method: Number(params.quantization_bit) === 4 ? "qlora" : "lora",
    learningRate: numberValue("learning_rate", defaults.learningRate),
    epochs: numberValue("num_train_epochs", defaults.epochs),
    cutoffLen: numberValue("cutoff_len", defaults.cutoffLen),
    batchSize: numberValue("per_device_train_batch_size", defaults.batchSize),
    gradientAccumulationSteps: numberValue("gradient_accumulation_steps", defaults.gradientAccumulationSteps),
    valSize: numberValue("val_size", defaults.valSize),
    scheduler: typeof params.lr_scheduler_type === "string" ? params.lr_scheduler_type : defaults.scheduler,
    warmupRatio: numberValue("warmup_ratio", defaults.warmupRatio),
    precision: params.bf16 ? "bf16" : params.fp16 ? "fp16" : "auto",
    loraRank: numberValue("lora_rank", defaults.loraRank),
    loraAlpha: numberValue("lora_alpha", defaults.loraAlpha),
    loraDropout: numberValue("lora_dropout", defaults.loraDropout),
    loraTarget: typeof params.lora_target === "string" ? params.lora_target : defaults.loraTarget,
    maxGradNorm: numberValue("max_grad_norm", defaults.maxGradNorm),
    seed: numberValue("seed", defaults.seed),
    gradientCheckpointing: params.gradient_checkpointing !== false,
    flashAttention: params.flash_attn === "disabled" ? "disabled" : "auto",
    ropeScaling: ["linear", "dynamic"].includes(String(params.rope_scaling)) ? params.rope_scaling as "linear" | "dynamic" : "none",
    loggingSteps: numberValue("logging_steps", defaults.loggingSteps),
    evalSteps: numberValue("eval_steps", defaults.evalSteps),
    saveSteps: numberValue("save_steps", defaults.saveSteps),
    saveTotalLimit: numberValue("save_total_limit", defaults.saveTotalLimit),
    maxSamples: params.max_samples === undefined ? null : numberValue("max_samples", 0),
    packing: Boolean(params.packing),
    preprocessingWorkers: numberValue("preprocessing_num_workers", defaults.preprocessingWorkers),
    dataloaderWorkers: numberValue("dataloader_num_workers", defaults.dataloaderWorkers),
    poolId: typeof environment.resource_pool_id === "string" ? environment.resource_pool_id : "",
    nodeId: typeof environment.node_id === "string" ? environment.node_id : "",
  };
}

function applyPipelineParams(pipeline: TrainingPipelineRecord) {
  const params = pipeline.params_template ?? {};
  const environment = pipeline.default_environment ?? {};
  form.epochs = numberParam(params.epochs, form.epochs);
  form.batch = numberParam(params.batch_size ?? params.batch, form.batch);
  form.lr0 = stringParam(params.learning_rate ?? params.lr0, form.lr0);
  form.image_size = numberParam(params.image_size, form.image_size);
  form.workers = numberParam(params.workers, form.workers);
  form.amp = params.amp !== false;
  form.warmup_epochs = numberParam(params.warmup_epochs ?? params.warmup_steps, form.warmup_epochs);
  form.save_period = numberParam(params.save_period, form.save_period);
  form.pretrained = params.pretrained === false ? "false" : "official";
  form.resume_weight = params.resume ? "last.pt" : "";
  if (typeof environment.device === "string" || typeof environment.device === "number") {
    form.device = String(environment.device);
    trainingEnvironment.mode = form.device === "cpu" ? "local" : "remote";
  }
}

function buildConfigText() {
  if (isPaddleXFramework.value) {
    const params = paddlexTrainingParams();
    const keys = ["epochs", "batch_size", "learning_rate", "image_size", "workers", "amp", "resume"];
    return [
      "# PaddleX object detection train config",
      "# 模型配置、数据集目录、输出目录和 GPU 设备由 Visiox 管理。",
      ...keys.map((key) => `${key}: ${formatConfigValue(params[key])}`),
    ].join("\n");
  }
  const merged = {
    ...yoloParamDefaults,
    ...configParams.value,
    ...trainingParams(),
  };
  return [
    "# Ultralytics YOLO26 train config",
    "# model/data/project/name/exist_ok/task/mode/device/workers are managed by Visiox.",
    ...yoloParamKeys.map((key) => `${key}: ${formatConfigValue(merged[key])}`),
  ].join("\n");
}

function applyConfigText() {
  if (isPaddleXFramework.value) {
    const allowed = new Set(["epochs", "batch_size", "learning_rate", "image_size", "workers", "amp", "resume"]);
    const next = parseConfigObject(configText.value, allowed);
    configParams.value = next;
    form.epochs = numberParam(next.epochs, form.epochs);
    form.batch = numberParam(next.batch_size, form.batch);
    form.lr0 = stringParam(next.learning_rate, form.lr0);
    form.image_size = numberParam(next.image_size, form.image_size);
    form.workers = numberParam(next.workers, form.workers);
    form.amp = next.amp !== false;
    form.resume_weight = next.resume ? "last.pt" : "";
    return;
  }
  const parsed = parseConfigObject(configText.value, new Set([...yoloParamKeys, "warmup_steps"]));
  const next = Object.fromEntries(Object.entries(parsed).map(([key, value]) => [
    key === "warmup_steps" ? "warmup_epochs" : key,
    value,
  ]));
  configParams.value = normalizeConfigParams(next);
  syncCommonFieldsFromConfig(configParams.value);
}

function parseConfigObject(text: string, allowed: Set<string>) {
  const parsed = parseYaml(text);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("配置文件必须是参数键值对象");
  }
  const values = parsed as Record<string, unknown>;
  const unknown = Object.keys(values).filter((key) => !allowed.has(key));
  if (unknown.length) throw new Error(`当前框架不支持配置项：${unknown.join("、")}`);
  return removeEmptyParams(values);
}

function toggleConfigMode() {
  if (configMode.value) {
    if (!commitConfigEditor()) return;
    configMode.value = false;
    return;
  }
  configText.value = buildConfigText();
  configMode.value = true;
}

function commitConfigEditor() {
  try {
    applyConfigText();
    return true;
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "配置文件解析失败"));
    return false;
  }
}

async function toggleFavorite(pipeline: TrainingPipelineRecord) {
  try {
    const updated = await api.updatePipeline(pipeline.id, { is_favorite: !pipeline.is_favorite });
    replacePipeline(updated);
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "收藏状态更新失败"));
  }
}

function openRenameDialog(pipeline: TrainingPipelineRecord) {
  editingPipelineId.value = pipeline.id;
  renameName.value = pipeline.name;
  renameDialogVisible.value = true;
}

async function confirmRename() {
  const name = renameName.value.trim();
  if (!editingPipelineId.value || !name) return;
  try {
    const updated = await api.updatePipeline(editingPipelineId.value, { name });
    replacePipeline(updated);
    renameDialogVisible.value = false;
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "产线名称修改失败"));
  }
}

function openPublicDialog(pipeline: TrainingPipelineRecord) {
  sharingPipeline.value = pipeline;
  publicDialogVisible.value = true;
}

function handlePipelineSharingSaved(visibility: string) {
  if (!sharingPipeline.value) return;
  const updated = {
    ...sharingPipeline.value,
    visibility,
    is_public: visibility === "organization",
  };
  replacePipeline(updated);
  sharingPipeline.value = updated;
}

async function deletePipeline(pipeline: TrainingPipelineRecord) {
  try {
    await ElMessageBox.confirm(`确认删除产线「${pipeline.name}」？`, "删除产线", { type: "warning" });
    await api.deletePipeline(pipeline.id);
    pipelines.value = pipelines.value.filter((item) => item.id !== pipeline.id);
    ElMessage.success("已删除产线");
  } catch (error) {
    if (getErrorMessage(error, "") !== "cancel") ElMessage.error(getErrorMessage(error, "删除产线失败"));
  }
}

async function deleteSelectedPipelines() {
  const ids = Array.from(selectedPipelineIds.value);
  if (ids.length === 0) {
    ElMessage.warning("请选择要删除的产线");
    return;
  }
  try {
    await ElMessageBox.confirm(`确认删除已选 ${ids.length} 条产线？`, "批量删除", { type: "warning" });
    await Promise.all(ids.map((id) => api.deletePipeline(id)));
    pipelines.value = pipelines.value.filter((pipeline) => !selectedPipelineIds.value.has(pipeline.id));
    selectedPipelineIds.value = new Set();
    ElMessage.success("已批量删除产线");
  } catch (error) {
    if (getErrorMessage(error, "") !== "cancel") ElMessage.error(getErrorMessage(error, "批量删除失败"));
  }
}

function toggleSelectedPipeline(id: string) {
  const next = new Set(selectedPipelineIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selectedPipelineIds.value = next;
}

function replacePipeline(updated: TrainingPipelineRecord) {
  pipelines.value = pipelines.value.map((pipeline) => (pipeline.id === updated.id ? { ...pipeline, ...updated } : pipeline));
}

function latestJobsByPipelineId(jobs: TrainingJobRecord[]) {
  return jobs.reduce<Record<string, TrainingJobRecord>>((memo, job) => {
    const current = memo[job.pipeline_id];
    const currentTime = Date.parse(current?.updated_at || current?.created_at || "");
    const nextTime = Date.parse(job.updated_at || job.created_at || "");
    if (!current || nextTime >= currentTime) memo[job.pipeline_id] = job;
    return memo;
  }, {});
}

function modelLabel(model: BaseModelRecord) {
  if (model.family.startsWith("custom-") && model.source_path?.startsWith("upload://")) {
    return model.source_path.slice("upload://".length);
  }
  return model.filename || `${model.family}-${model.task}-${model.scale}`;
}

function normalizeWeightOptionName(value?: string | null) {
  const text = String(value || "").trim();
  if (!text) return "";
  const filename = text.split(/[\\/]/).pop() || text;
  if (filename === "best" || filename === "last") return `${filename}.pt`;
  if (filename === "best.pt" || filename === "last.pt") return filename;
  return filename.endsWith(".pt") ? filename : "";
}

function trainedModelSourceWeight(model: TrainedModelRecord) {
  return (
    normalizeWeightOptionName(model.version) ||
    normalizeWeightOptionName(model.artifact_uri) ||
    normalizeWeightOptionName(model.name)
  );
}

function trainedModelDeploymentName(model: TrainedModelRecord) {
  const value = model.metrics?.deployment_name;
  return typeof value === "string" ? value.trim() : "";
}

function defaultDeploymentName(sourceWeight: string) {
  const base = sourceWeight.replace(/\.pt$/i, "");
  return base ? `${base}_model` : "best_model";
}

function weightOrder(weight: string) {
  if (weight === "best.pt") return 0;
  if (weight === "last.pt") return 1;
  return 2;
}

function taskLabel(task?: string) {
  const labels: Record<string, string> = {
    detect: "目标检测",
    segment: "图像分割",
    semantic: "语义分割",
    pose: "关键点检测",
    obb: "旋转框检测",
    classify: "图像分类",
    ocr: "通用OCR",
    document: "文档图像信息抽取",
    table: "通用表格识别",
    timeseries: "时序分析",
    attribute: "属性识别",
    llm: "大模型训练",
  };
  return labels[task || ""] || task || "未知类型";
}

function scenarioKeyForTask(task?: string) {
  return scenarios.find((scenario) => scenario.task === task)?.key || "detect";
}

function pendingCapabilityPipelineFields(task: string) {
  return {
    task,
    task_kind: task,
    framework: "pending",
    adapter_key: `pending.${task}.v1`,
    adapter_version: "1.0.0",
    model_family: "pending",
    recipe: { capability_status: "pending" },
  };
}

function numberParam(value: unknown, fallback: number) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringParam(value: unknown, fallback: string) {
  return typeof value === "number" || typeof value === "string" ? String(value) : fallback;
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    ready: "配置中",
    running: "训练中",
    success: "运行成功",
    completed: "运行成功",
    failed: "运行中止",
    canceled: "运行中止",
    draft: "配置中",
  };
  return labels[status || ""] || "配置中";
}

function statusClass(status?: string): "pending" | "running" | "success" | "danger" {
  if (status === "running") return "running";
  if (["success", "completed"].includes(status || "")) return "success";
  if (["failed", "canceled"].includes(status || "")) return "danger";
  return "pending";
}

function isConfiguredPipeline(pipeline: TrainingPipelineRecord) {
  return !["running", "failed", "canceled", "success", "completed"].includes(pipeline.status || "");
}

function isTrainingPipeline(pipeline: TrainingPipelineRecord) {
  return pipeline.status === "running";
}

function runtimeText(job?: TrainingJobRecord) {
  const start = parseTime(job?.started_at || job?.created_at);
  const end = parseTime(job?.finished_at || job?.updated_at);
  if (!start || !end || end < start) return "0天0小时0分钟0秒";
  let seconds = Math.floor((end - start) / 1000);
  const days = Math.floor(seconds / 86400);
  seconds %= 86400;
  const hours = Math.floor(seconds / 3600);
  seconds %= 3600;
  const minutes = Math.floor(seconds / 60);
  seconds %= 60;
  return `${days}天${hours}小时${minutes}分钟${seconds}秒`;
}

function readMetricScore(metrics: Record<string, unknown>) {
  const candidates = ["mAP50", "mAP50(B)", "metrics/mAP50(B)", "map50", "fitness"];
  for (const key of candidates) {
    const value = metrics[key];
    if (typeof value === "number") return formatMetricValue(value);
    if (typeof value === "string" && value.trim()) return value;
  }
  return "-";
}

function formatEvaluationScore(value?: number | null) {
  return typeof value === "number" ? formatMetricValue(value) : "-";
}

function evaluationScoreForWeight(weight: string) {
  const records = [...(latestEvaluation.value ? [latestEvaluation.value] : []), ...evaluationHistory.value];
  const record = records.find((item) => item.model_weight === weight);
  if (record) {
    const score = formatEvaluationScore(record.score);
    return score === "-" ? readMetricScore(record.metrics) : score;
  }
  return "-";
}

function formatMetricValue(value: unknown) {
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
  }
  if (typeof value === "string" && value.trim()) return value;
  if (value === null || value === undefined) return "-";
  return JSON.stringify(value);
}

function formatEvaluationRecordTitle(record: PipelineEvaluationResponse) {
  return `${formatTime(record.created_at)} ${shortId(record.id)}`;
}

function shortId(value: string) {
  return value.length > 12 ? `${value.slice(0, 12)}...` : value;
}

function evaluationStatusLabel(status?: string) {
  const labels: Record<string, string> = {
    completed: "评估完成",
    success: "评估完成",
    failed: "评估失败",
    running: "评估中",
  };
  return labels[status || ""] || status || "-";
}

function evaluationDatasetName(datasetId?: string) {
  if (!datasetId) return "-";
  return datasets.value.find((dataset) => dataset.id === datasetId)?.name ?? datasetId;
}

function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (item: number) => String(item).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function parseTime(value?: string) {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function sampleImageUrl(sample: DatasetSampleRecord) {
  return api.datasetSampleContentUrl(sample.dataset_id, sample.id);
}

function splitStats(count: number, total: number) {
  return {
    count,
    percent: total > 0 ? Number(((count / total) * 100).toFixed(2)) : 0,
  };
}

function classDistributionFromSchema() {
  const names = selectedDataset.value?.class_schema?.names;
  if (!Array.isArray(names)) return {};
  return Object.fromEntries(names.map((name) => [String(name), 0]));
}

function readAnalysisResult(payload?: Record<string, unknown>): DatasetAnalysis {
  const result = payload?.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return {};
  return result as DatasetAnalysis;
}

function barPercent(value: number) {
  return Math.max(1, (value / classChartMax.value) * 100);
}

function normalizeRows<T>(payload: { items?: T[] } | T[]): T[] {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload.items) ? payload.items : [];
}

function removeEmptyParams(params: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(params).filter(([, value]) => value !== "" && value !== undefined && value !== null));
}

function normalizeConfigParams(params: Record<string, unknown>) {
  const normalized: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(params)) {
    const paramKey = key === "warmup_steps" ? "warmup_epochs" : key;
    if (!yoloParamKeys.includes(paramKey) || value === undefined || value === null || value === "") continue;
    normalized[paramKey] = value;
  }
  return normalized;
}

function syncCommonFieldsFromConfig(params: Record<string, unknown>) {
  form.epochs = numberParam(params.epochs, form.epochs);
  form.batch = numberParam(params.batch, form.batch);
  form.lr0 = stringParam(params.lr0, form.lr0);
  form.warmup_epochs = numberParam(params.warmup_epochs, form.warmup_epochs);
  form.save_period = numberParam(params.save_period, form.save_period);
  form.pretrained = params.pretrained === false ? "false" : "official";
  form.resume_weight = params.resume ? "last.pt" : "";
}

function formatConfigValue(value: unknown) {
  if (value === undefined || value === null || value === "") return "null";
  if (Array.isArray(value)) return `[${value.join(", ")}]`;
  return String(value);
}

function unwrapItems<T>(result: PromiseSettledResult<{ items?: T[] }>, label: string): T[] {
  if (result.status === "fulfilled") return Array.isArray(result.value.items) ? result.value.items : [];
  errorMessage.value = errorMessage.value ? `${errorMessage.value} ${label}加载失败。` : `${label}加载失败。`;
  return [];
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
</script>

<style scoped>
.model-space-view {
  container: model-space / inline-size;
  min-height: 100%;
  min-width: 0;
  color: #111827;
}

.model-space-view.list-mode {
  display: flex;
  flex-direction: column;
}

.page-header,
.wizard-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 22px;
}

.page-header h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
}

.header-actions,
.toolbar-controls,
.card-actions,
.wizard-footer {
  display: flex;
  align-items: center;
  gap: 12px;
}

.page-alert {
  margin-bottom: 16px;
}

.list-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #eef1f6;
}

.tabs {
  display: flex;
  gap: 28px;
}

.tab-button {
  height: 44px;
  border: 0;
  border-bottom: 3px solid transparent;
  background: transparent;
  color: #344054;
  font-size: 16px;
  cursor: pointer;
}

.tab-button.active {
  border-bottom-color: #2f7df6;
  color: #1763ff;
  font-weight: 700;
}

.toolbar-select {
  width: 124px;
}

.search-input {
  width: 280px;
}

.pipeline-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 20px 16px;
  margin-top: 20px;
}

.pipeline-card {
  position: relative;
  min-height: 138px;
  padding: 22px 20px 16px;
  border: 1px solid var(--visiox-card-border);
  border-radius: var(--visiox-card-radius);
  background: var(--visiox-card-surface);
  box-shadow: none;
  transition: border-color 0.15s ease;
}

.pipeline-card:not(.skeleton-card):hover {
  border-color: #aeb7c3;
  box-shadow: none;
  transform: none;
}

.pipeline-card.selected {
  border-color: #2f7df6;
}

.card-actions {
  position: absolute;
  top: 10px;
  right: 10px;
  opacity: 0;
  transition: opacity 0.16s ease;
}

.pipeline-card:hover .card-actions,
.pipeline-card.selected .card-actions {
  opacity: 1;
}

.icon-button {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: #fff;
  color: #667085;
  cursor: pointer;
}

.icon-button:hover {
  background: #f2f4f7;
  color: #1763ff;
}

.more-wrap {
  position: relative;
}

.more-menu {
  position: absolute;
  top: 30px;
  right: 0;
  z-index: 20;
  display: none;
  min-width: 104px;
  padding: 6px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 8px 24px rgb(15 23 42 / 12%);
}

.more-wrap:hover .more-menu {
  display: grid;
}

.more-menu button {
  border: 0;
  background: transparent;
  padding: 8px 10px;
  text-align: left;
  cursor: pointer;
}

.danger-text {
  color: #f04438;
}

.card-main h2 {
  overflow: hidden;
  margin: 0 0 14px;
  font-size: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-main time {
  display: block;
  margin-bottom: 12px;
  color: #475467;
  font-size: 14px;
}

.type-pill,
.status-pill,
.owner-pill,
.selected-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border-radius: 18px;
  padding: 6px 11px;
  font-size: 13px;
}

.type-pill {
  background: #f3f6fb;
  color: #344054;
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 18px;
}

.status-action-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.status-pill.pending {
  background: #eedcff;
  color: #8b38ff;
}

.status-pill.running {
  background: #e8f1ff;
  color: #1763ff;
}

.status-pill.success {
  background: #d7f8df;
  color: #12a14a;
}

.status-pill.danger {
  background: #ffdedd;
  color: #f04438;
}

.stop-training-button {
  position: absolute;
  left: 0;
  top: 30px;
  z-index: 25;
  display: none;
  min-width: 84px;
  border: 1px solid #ffd0cc;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.12);
  color: #f04438;
  cursor: pointer;
  font-size: 13px;
  padding: 7px 10px;
}

.stop-training-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.status-action-wrap:hover .stop-training-button {
  display: inline-flex;
}

.owner-pill {
  background: #fff0df;
  color: #ff7a1a;
}

.pagination-row {
  position: sticky;
  bottom: 0;
  z-index: 5;
  display: flex;
  flex: 0 0 auto;
  justify-content: flex-end;
  align-items: center;
  gap: 18px;
  margin-top: auto;
  padding: 20px 0 4px;
  background: #fff;
}

.pagination-row :deep(.el-pagination.is-background .el-pager li),
.pagination-row :deep(.el-pagination.is-background .btn-prev),
.pagination-row :deep(.el-pagination.is-background .btn-next) {
  background: #fff;
  color: #344054;
}

.pagination-row :deep(.el-pagination.is-background .el-pager li.is-active) {
  border: 1px solid #e6e9ef;
  background: #f5f7fa;
  color: #5b9cf6;
  font-weight: 500;
}

.total-count {
  color: #101828;
}

.skeleton-title,
.skeleton-line,
.skeleton-pill {
  border-radius: 4px;
  background: #eef2f7;
}

.skeleton-title {
  width: 44%;
  height: 18px;
}

.skeleton-line {
  width: 62%;
  height: 14px;
  margin-top: 18px;
}

.skeleton-pill {
  width: 80px;
  height: 28px;
  margin-top: 16px;
  border-radius: 18px;
}

.wizard-topbar {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  min-height: 74px;
  padding-top: 8px;
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  position: absolute;
  left: 0;
  top: 10px;
  border: 0;
  background: transparent;
  color: #1763ff;
  cursor: pointer;
}

.wizard-steps {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  width: min(640px, 56vw);
  margin: 0 auto;
}

.wizard-steps button {
  position: relative;
  display: grid;
  justify-items: center;
  gap: 8px;
  border: 0;
  background: transparent;
  color: #98a2b3;
  cursor: pointer;
  font-size: 14px;
  padding: 0;
}

.wizard-steps button::before {
  content: "";
  position: absolute;
  top: 10px;
  left: calc(-50% + 12px);
  width: calc(100% - 24px);
  height: 2px;
  background: #98a2b3;
}

.wizard-steps button:first-child::before {
  display: none;
}

.wizard-steps span {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 2px solid #98a2b3;
  border-radius: 50%;
  background: #f5f7fb;
  color: #667085;
  font-weight: 700;
  line-height: 1;
}

.wizard-steps strong {
  font-size: 15px;
  font-weight: 500;
}

.wizard-steps button.active,
.wizard-steps button.done {
  color: #111827;
}

.wizard-steps button.active span,
.wizard-steps button.done span {
  border-color: #111827;
  color: #111827;
}

.wizard-steps button.done::before,
.wizard-steps button.active::before {
  background: #111827;
}

.wizard-panel {
  min-height: 620px;
  background: #fff;
  padding: 28px 32px;
  border: 1px solid #edf0f5;
}

.step-panel h2 {
  margin: 0 0 24px;
  font-size: 20px;
}

.scenario-card,
.scenario-option {
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.scenario-card {
  display: grid;
  width: 280px;
  gap: 12px;
  padding: 22px;
}

.scenario-card.selected,
.scenario-option.selected {
  border-color: #1763ff;
  box-shadow: 0 0 0 1px #1763ff inset;
}

.scenario-card em {
  width: fit-content;
  border-radius: 12px;
  background: #eef5ff;
  padding: 4px 10px;
  color: #1763ff;
  font-style: normal;
}

.scenario-card a {
  color: #1763ff;
}

.data-step {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 32px;
}

.field-label,
.param-field span {
  display: block;
  margin: 18px 0 8px;
  color: #111827;
  font-weight: 500;
}

.required::before,
.param-field span::before {
  content: "* ";
  color: #f04438;
}

.full-input,
.environment-select {
  width: 540px;
  max-width: 100%;
}

.framework-step {
  max-width: 1120px;
}

.framework-step__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 22px;
}

.framework-step__heading h2 {
  margin-bottom: 6px;
}

.framework-step__heading p {
  margin: 0;
  color: #667085;
  font-size: 14px;
}

.framework-step__heading > span {
  border-radius: 999px;
  background: #f2f4f7;
  padding: 6px 11px;
  color: #344054;
  font-size: 13px;
  white-space: nowrap;
}

.framework-step__layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  align-items: start;
  gap: 18px;
}

.pipeline-summary {
  display: grid;
  align-content: start;
  gap: 10px;
  min-width: 0;
  border: 1px solid #e1e5eb;
  border-radius: 8px;
  background: #fafafa;
  padding: 18px;
}

.pipeline-summary__eyebrow {
  color: #667085;
  font-size: 12px;
}

.pipeline-summary > strong {
  overflow: hidden;
  color: #101828;
  font-size: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pipeline-summary > p {
  min-height: 40px;
  margin: 0;
  color: #667085;
  font-size: 13px;
  line-height: 1.55;
}

.pipeline-summary dl {
  display: grid;
  gap: 9px;
  margin: 6px 0 0;
  border-top: 1px solid #e4e7ec;
  padding-top: 13px;
}

.pipeline-summary dl > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.pipeline-summary dt,
.pipeline-summary dd {
  margin: 0;
  font-size: 12px;
}

.pipeline-summary dt { color: #667085; }
.pipeline-summary dd { color: #101828; font-weight: 600; }

.pending-capability {
  max-width: 720px;
  margin-bottom: 20px;
  padding: 18px 20px;
  border: 1px solid #d9dee8;
  border-radius: 6px;
  background: #f7f8fa;
}

.pending-capability strong { color: #111827; font-size: 16px; }
.pending-capability p { margin: 8px 0 0; color: #667085; line-height: 1.7; }

.selected-framework-model {
  display: grid;
  width: min(100%, 540px);
  min-height: 68px;
  box-sizing: border-box;
  align-content: center;
  gap: 5px;
  padding: 12px 14px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  background: var(--visiox-card-surface, #f7f8fa);
}

.selected-framework-model span {
  color: #667085;
  font-size: 13px;
}

.training-target-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 280px));
  gap: 12px;
  margin: 18px 0 24px;
}

.training-target-switch button {
  display: grid;
  gap: 7px;
  min-height: 76px;
  padding: 14px 16px;
  border: 1px solid #d8dee9;
  border-radius: 4px;
  background: #fff;
  color: #202938;
  cursor: pointer;
  text-align: left;
}

.training-target-switch button.active,
.training-resource-card.selected {
  border-color: #1763ff;
  background: #f5f8ff;
  box-shadow: 0 0 0 1px #1763ff inset;
}

.training-target-switch span,
.training-resource-heading span,
.training-resource-card small,
.training-allocation-row small,
.training-runtime-config small,
.local-training-summary small {
  color: #667085;
  font-size: 13px;
  font-style: normal;
  line-height: 1.45;
}

.local-training-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  width: min(100%, 572px);
  padding: 14px 16px;
  border: 1px solid #dfe5ef;
  background: #f8fafc;
}

.local-training-summary div {
  display: grid;
  gap: 4px;
}

.resource-status-dot {
  width: 9px;
  height: 9px;
  flex: 0 0 9px;
  border-radius: 50%;
  background: #98a2b3;
}

.resource-status-dot.online {
  background: #12b76a;
  box-shadow: 0 0 0 3px #d1fadf;
}

.training-resource-section {
  width: min(100%, 940px);
  display: grid;
  gap: 22px;
}

.training-resource-section > p {
  color: #667085;
}

.training-resource-group {
  display: grid;
  gap: 10px;
}

.training-resource-heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.training-resource-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.training-resource-card {
  display: grid;
  gap: 7px;
  min-height: 72px;
  padding: 12px 14px;
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  background: #fff;
  color: #202938;
  cursor: pointer;
  text-align: left;
}

.training-node-card {
  min-height: 104px;
}

.resource-card-title {
  display: flex;
  align-items: center;
  gap: 9px;
}

.resource-card-title em {
  margin-left: auto;
  padding: 2px 7px;
  border-radius: 3px;
  background: #ecfdf3;
  color: #027a48;
  font-size: 12px;
  font-style: normal;
}

.training-allocation-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  max-width: 560px;
  padding: 14px 16px;
  border: 1px solid #dfe5ef;
  background: #f8fafc;
}

.training-allocation-row > div {
  display: grid;
  gap: 4px;
}

.training-runtime-config {
  max-width: 720px;
  border-top: 1px solid #e4e7ec;
  padding-top: 14px;
}

.training-runtime-config summary {
  color: #344054;
  cursor: pointer;
  font-weight: 600;
}

.training-runtime-config label {
  display: grid;
  gap: 8px;
  margin-top: 14px;
}

@media not all {
  .training-target-switch,
  .training-resource-options {
    grid-template-columns: 1fr;
  }

  .training-allocation-row,
  .training-resource-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}

.dataset-picker {
  display: flex;
  align-items: center;
  gap: 12px;
}

.dataset-tab,
.analysis-tabs button,
.dialog-tabs button {
  border: 1px solid #dfe5ef;
  background: #fff;
  padding: 10px 18px;
  cursor: pointer;
}

.dataset-tab.active,
.analysis-tabs .active,
.dialog-tabs .active {
  color: #1763ff;
  background: #f3f7ff;
  border-color: #cfe0ff;
}

.dataset-select {
  width: 360px;
}

.selected-chip {
  margin-top: 12px;
  background: #f3f7ff;
  color: #1763ff;
}

.processing-box,
.analysis-box,
.step-aside {
  margin-top: 24px;
  border: 1px solid #dfe5ef;
  padding: 18px;
}

.processing-title,
.analysis-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: #475467;
}

.analysis-box h3,
.step-aside h3 {
  margin: 0 0 14px;
}

.analysis-tabs {
  display: flex;
  margin: 22px 0;
}

.sample-preview {
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  display: grid;
  gap: 16px;
  padding: 16px 18px;
}

.loading-preview {
  align-items: center;
  color: #667085;
  min-height: 180px;
}

.sample-canvas {
  align-items: center;
  background: #f4f7fb;
  display: flex;
  justify-content: center;
  min-height: 320px;
}

.sample-canvas img {
  display: block;
  max-height: 320px;
  max-width: 100%;
  object-fit: contain;
}

.thumbnail-row {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.thumb {
  border-radius: 4px;
  cursor: pointer;
  height: 58px;
  flex: 0 0 auto;
  width: 70px;
  border: 1px solid transparent;
  background: #f4f7fb;
  padding: 3px;
}

.thumb.active {
  border-color: #1763ff;
}

.thumb img {
  display: block;
  height: 100%;
  object-fit: cover;
  width: 100%;
}

.class-chart {
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  min-height: 360px;
  padding: 18px 20px 20px;
}

.chart-shell {
  position: relative;
}

.chart-legend {
  position: absolute;
  right: 10px;
  top: 6px;
  z-index: 2;
  display: grid;
  gap: 6px;
  border: 1px solid #dfe5ef;
  background: #fff;
  padding: 7px 10px;
  color: #344054;
  font-size: 13px;
}

.chart-legend span {
  display: flex;
  align-items: center;
  gap: 7px;
}

.chart-legend i {
  display: inline-block;
  height: 10px;
  width: 20px;
}

.chart-legend .train,
.bar.train {
  background: #1f77b4;
}

.chart-legend .val,
.bar.val {
  background: #ff7f0e;
}

.chart-frame {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  min-height: 300px;
}

.chart-y-axis {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 0 8px 34px 0;
  color: #344054;
  font-size: 12px;
  text-align: right;
}

.chart-y-axis::before {
  content: "Counts";
  position: absolute;
  left: -2px;
  top: 45%;
  color: #344054;
  font-size: 13px;
  transform: rotate(-90deg);
  transform-origin: left center;
}

.chart-plot {
  --class-count: 1;
  position: relative;
  display: grid;
  grid-template-columns: repeat(var(--class-count), minmax(54px, 1fr));
  align-items: end;
  gap: 18px;
  min-height: 300px;
  border: 1px solid #7a8798;
  background: #fff;
  padding: 18px 22px 34px;
}

.chart-grid {
  position: absolute;
  inset: 18px 0 34px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  pointer-events: none;
}

.chart-grid span {
  border-top: 1px solid #eef2f7;
}

.bar-group {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-rows: minmax(0, 1fr) 22px;
  height: 100%;
  min-height: 248px;
}

.bar-pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(14px, 1fr));
  align-items: end;
  gap: 3px;
}

.bar {
  display: block;
  min-height: 1px;
}

.chart-x-label {
  align-self: end;
  color: #344054;
  font-size: 12px;
  overflow: hidden;
  text-align: center;
  text-overflow: ellipsis;
  transform: rotate(-90deg);
  white-space: nowrap;
}

.step-aside {
  margin-top: 0;
  background: #fbfcff;
}

.step-aside p {
  color: #667085;
  line-height: 1.7;
}

.step-aside table {
  width: 100%;
  border-collapse: collapse;
}

.step-aside th,
.step-aside td {
  border-bottom: 1px solid #e5eaf2;
  padding: 12px 0;
  text-align: left;
}

.params-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.params-header span {
  margin-left: 8px;
  color: #667085;
  font-size: 14px;
  font-weight: 400;
}

.link-button {
  border: 0;
  background: transparent;
  color: #1763ff;
  cursor: pointer;
}

.params-form {
  max-width: 560px;
}

.param-field {
  display: grid;
  gap: 8px;
  margin-bottom: 24px;
}

.param-field small {
  color: #667085;
}

.advanced-config {
  margin-top: 26px;
}

.advanced-config summary {
  margin-bottom: 18px;
  cursor: pointer;
  font-weight: 700;
}

.config-editor {
  width: 100%;
  min-height: 540px;
  border: 1px solid #1763ff;
  padding: 12px 18px;
  font-family: Consolas, monospace;
  font-size: 13px;
  line-height: 1.8;
}

.submit-summary {
  display: grid;
  width: 520px;
  gap: 12px;
  margin-top: 24px;
  border: 1px solid #dfe5ef;
  padding: 18px;
  color: #475467;
}

.wizard-footer {
  justify-content: flex-end;
  margin-top: 28px;
}

.dialog-tabs {
  display: flex;
  justify-content: center;
  gap: 30px;
  margin-bottom: 24px;
  border-bottom: 1px solid #e7ebf2;
}

.dialog-tabs button {
  min-width: 112px;
  border: 0;
  border-bottom: 2px solid transparent;
  padding: 0 8px 13px;
  background: transparent;
  color: #475467;
  cursor: pointer;
}

.dialog-tabs button.active {
  border-bottom-color: #2563eb;
  color: #2563eb;
  font-weight: 600;
}

.scenario-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  max-height: 390px;
  padding: 1px 6px 1px 1px;
  overflow: auto;
}

.scenario-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  min-height: 96px;
  padding: 15px 14px;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, background-color 0.16s ease;
}

.scenario-option:hover {
  border-color: #a8c2f5;
  background: #fbfdff;
}

.scenario-option:focus-visible {
  outline: 2px solid #93b4ff;
  outline-offset: 2px;
}

.scenario-copy {
  display: grid;
  min-width: 0;
  gap: 7px;
}

.scenario-copy > span {
  display: -webkit-box;
  overflow: hidden;
  color: #667085;
  font-size: 13px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.scenario-icon {
  display: grid;
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  place-items: center;
  border-radius: 7px;
  color: #fff;
  font-size: 24px;
  box-shadow: 0 4px 10px rgb(37 99 235 / 20%);
}

.scenario-icon.blue {
  background: #3478f6;
}

.scenario-icon.green {
  background: #22a875;
  box-shadow: 0 4px 10px rgb(34 168 117 / 20%);
}

.local-model-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.local-model-fields > div {
  min-width: 0;
}

.upload-box {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  width: 100%;
  min-height: 104px;
  border: 1px dashed #b9c3d4;
  background: #fbfcff;
  color: #667085;
  cursor: pointer;
  transition: border-color 0.16s ease, background-color 0.16s ease;
}

.upload-box:hover,
.upload-box.selected {
  border-color: #6d9df8;
  background: #f6f9ff;
}

.upload-icon {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 6px;
  background: #eaf1ff;
  color: #2563eb;
  font-size: 22px;
}

.upload-copy {
  display: grid;
  max-width: calc(100% - 72px);
  gap: 5px;
  text-align: left;
}

.upload-copy strong,
.upload-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-copy strong {
  color: #344054;
}

.upload-copy small,
.local-model-hint {
  color: #7a8699;
  font-size: 12px;
}

.local-model-hint {
  margin: -4px 0 0;
  line-height: 1.5;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  clip-path: inset(50%);
}

.public-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #edf0f5;
  padding-bottom: 18px;
}

.public-switch-row p {
  margin: 8px 0 0;
  color: #667085;
}

.public-section-title {
  display: flex;
  justify-content: space-between;
  margin: 20px 0 14px;
  font-weight: 700;
}

.public-section-title span {
  color: #667085;
  font-weight: 400;
}

.permission-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px 28px;
}

.pipeline-detail-view {
  min-height: 720px;
  background: #fff;
  margin: -8px -12px 0;
  padding: 16px 40px 42px;
}

.detail-back {
  position: static;
  margin-bottom: 44px;
}

.pipeline-detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 36px;
}

.pipeline-detail-header h1 {
  margin: 0;
  font-size: 22px;
}

.pipeline-detail-header p {
  display: flex;
  align-items: center;
  gap: 20px;
  margin: 16px 0 0;
  color: #667085;
}

.pipeline-detail-header i {
  width: 1px;
  height: 16px;
  background: #98a2b3;
}

.detail-actions {
  display: flex;
  gap: 16px;
}

.detail-tabs {
  display: flex;
  gap: 34px;
  border-bottom: 1px solid #dfe5ef;
}

.detail-tabs button,
.detail-subtabs button,
.logs-toolbar button {
  border: 0;
  background: transparent;
  cursor: pointer;
}

.detail-tabs button {
  padding: 0 0 18px;
  color: #344054;
  font-size: 16px;
}

.detail-tabs button.active {
  border-bottom: 3px solid #1763ff;
  color: #1763ff;
  font-weight: 700;
}

.detail-panel {
  padding: 30px 0;
}

.info-lines {
  display: grid;
  gap: 20px;
  max-width: 680px;
  margin-left: 34px;
}

.info-lines p,
.param-line {
  display: grid;
  grid-template-columns: 128px minmax(0, 1fr);
  align-items: start;
  margin: 0;
}

.info-lines span,
.param-line > span {
  color: #344054;
  text-align: right;
  white-space: nowrap;
}

.info-lines strong {
  font-weight: 500;
}

.info-lines a,
.evaluation-table a,
.table-link {
  color: #1763ff;
  text-decoration: none;
}

.table-link {
  border: 0;
  background: transparent;
  cursor: pointer;
  padding: 0;
}

.result-files-trigger {
  justify-self: start;
  width: auto;
  text-align: left;
}

.param-table,
.evaluation-table {
  border-collapse: collapse;
  border: 1px solid #dfe5ef;
  background: #fff;
}

.param-table {
  width: 500px;
}

.param-table th,
.param-table td,
.evaluation-table th,
.evaluation-table td {
  border: 1px solid #edf0f5;
  padding: 10px 12px;
  font-weight: 400;
  text-align: left;
}

.param-table th,
.evaluation-table th {
  background: #f5f7fb;
  color: #344054;
}

.param-table td {
  text-align: right;
}

.logs-toolbar {
  display: flex;
  justify-content: flex-end;
  gap: 24px;
  margin-bottom: 24px;
}

.logs-toolbar button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #1763ff;
}

.training-log-panel {
  min-height: 500px;
  overflow: auto;
  border: 1px solid #cfd8e6;
  background: #f6f8fc;
  padding: 24px 20px;
  color: #111827;
  font-family: Consolas, "Courier New", monospace;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.experience-detail-panel {
  padding: 28px 0;
}

.detail-subtabs {
  display: flex;
  width: 360px;
  margin-bottom: 16px;
}

.detail-subtabs button {
  flex: 1;
  border: 1px solid #dfe5ef;
  padding: 12px;
}

.detail-subtabs button.active {
  background: #eff6ff;
  color: #1763ff;
  font-weight: 700;
}

.deploy-detail-panel,
.evaluate-detail-panel {
  max-width: 100%;
}

.deploy-field {
  display: grid;
  grid-template-columns: 204px minmax(0, 540px);
  align-items: center;
  gap: 10px;
  margin: 16px 0;
}

.evaluation-form label {
  display: grid;
  grid-template-columns: 120px minmax(0, 540px);
  align-items: center;
  gap: 10px;
  margin: 16px 0;
}

.deploy-field > span {
  text-align: right;
}

.required-label::before,
.required-row > span::before {
  content: "* ";
  color: #f04438;
}

.deploy-section {
  margin: 26px 0 18px;
}

.deploy-section h3,
.evaluate-detail-panel h3 {
  margin: 0 0 16px;
  font-size: 18px;
}

.deploy-section p {
  width: 720px;
  border-radius: 4px;
  background: #f5f8ff;
  color: #667085;
  padding: 12px 16px;
}

.detail-select {
  width: 540px;
  margin-left: 214px;
}

.weight-source-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  width: min(100%, 720px);
  margin-top: 18px;
  border: 1px solid #dfe5ef;
}

.weight-source-switch button {
  min-height: 44px;
  border: 0;
  border-right: 1px solid #dfe5ef;
  background: #fff;
  color: #344054;
  cursor: pointer;
}

.weight-source-switch button:last-child {
  border-right: 0;
}

.weight-source-switch button.active {
  background: #f3f7ff;
  color: #1763ff;
  font-weight: 600;
}

.deployment-weight-row {
  display: grid;
  grid-template-columns: 128px minmax(0, 560px);
  align-items: center;
  gap: 8px;
  margin-top: 18px;
}

.deployment-weight-row > span {
  text-align: right;
  white-space: nowrap;
}

.deployment-weight-row .detail-select {
  width: 100%;
  margin-left: 0;
}

.offline-model-row {
  display: grid;
  grid-template-columns: 128px minmax(0, 560px);
  align-items: center;
  gap: 8px;
  margin: 18px 0 0 48px;
}

.offline-model-row > span {
  text-align: right;
  white-space: nowrap;
}

.edge-resource-section,
.optimization-section {
  max-width: 940px;
  margin: 28px 0;
}

.edge-resource-section h3,
.optimization-section h3 {
  margin: 0 0 14px;
  font-size: 18px;
}

.edge-resource-section > p,
.deploy-error {
  color: #667085;
}

.legacy-environment-label {
  display: block;
  margin: 0 0 8px;
  color: #344054;
  font-size: 14px;
}

.deploy-error {
  color: #d92d20;
}

.edge-choice-row {
  display: grid;
  grid-template-columns: 118px repeat(2, minmax(240px, 1fr));
  align-items: stretch;
  gap: 12px;
  margin: 12px 0;
}

.edge-choice-row > span {
  align-self: center;
  text-align: right;
}

.edge-choice {
  display: grid;
  gap: 7px;
  min-height: 72px;
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  background: #fff;
  color: #344054;
  cursor: pointer;
  padding: 12px 14px;
  text-align: left;
}

.edge-choice.selected {
  border-color: #1763ff;
  box-shadow: 0 0 0 1px #1763ff inset;
}

.edge-choice small {
  color: #667085;
  line-height: 1.4;
}

.optimization-modes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.optimization-modes button {
  display: grid;
  gap: 8px;
  min-height: 76px;
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  padding: 14px 16px;
  text-align: left;
}

.optimization-modes button.active {
  border-color: #1763ff;
  background: #f7faff;
}

.optimization-modes span,
.optimization-summary {
  color: #667085;
  font-size: 13px;
}

.optimization-summary {
  display: flex;
  gap: 14px;
  margin-top: 12px;
  border-left: 3px solid #1763ff;
  background: #f5f8ff;
  padding: 10px 12px;
}

.optimization-summary span {
  color: #1763ff;
}

.manual-optimization-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 14px;
}

.manual-optimization-grid label {
  display: grid;
  gap: 7px;
  color: #344054;
  font-size: 14px;
}

.manual-optimization-grid input,
.manual-optimization-grid select {
  min-height: 38px;
  border: 1px solid #d0d5dd;
  border-radius: 4px;
  background: #fff;
  padding: 0 10px;
}

.env-note {
  display: flex;
  align-items: center;
  gap: 16px;
  margin: 24px 0 42px;
  color: #98a2b3;
}

.env-note strong {
  color: #344054;
}

.instance-name-input {
  width: 190px;
  margin-left: 18px;
}

.env-note :deep(.el-input__inner) {
  text-align: left;
}

.deploy-action {
  margin-left: 0;
}

.offline-export-button {
  margin-top: 72px;
}

.env-note strong {
  color: #111827;
}

.evaluation-table {
  width: 640px;
  margin-bottom: 34px;
}

.evaluation-form {
  display: grid;
  max-width: 1120px;
  gap: 16px;
}

.evaluation-history-panel {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 20px;
  min-height: 520px;
  width: 100%;
}

.evaluation-history-list {
  border-right: 1px solid #dfe5ef;
  padding: 14px 24px 0 0;
}

.evaluation-history-list h3 {
  margin: 0 0 16px;
  font-size: 18px;
}

.evaluation-history-list button {
  display: block;
  width: 100%;
  min-height: 40px;
  border: 0;
  background: transparent;
  color: #111827;
  cursor: pointer;
  font-size: 15px;
  padding: 8px 14px;
  text-align: left;
}

.evaluation-history-list button:hover,
.evaluation-history-list button.active {
  background: #f3f7ff;
}

.evaluation-history-list p,
.history-empty {
  color: #98a2b3;
}

.evaluation-history-detail {
  min-height: 260px;
  border: 1px solid #cfd8e6;
  background: #f5f8ff;
  margin-top: 14px;
  padding: 30px;
  min-width: 0;
  overflow-x: auto;
}

.evaluation-history-detail h3 {
  margin: 0 0 18px;
}

.evaluation-history-detail dl + h3 {
  margin-top: 20px;
}

.evaluation-history-detail dl {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  gap: 16px 18px;
  margin: 0;
  color: #344054;
}

.evaluation-history-detail dt,
.evaluation-history-detail dd {
  margin: 0;
}

.evaluation-history-detail dd {
  color: #0f172a;
}

.history-status {
  color: #1763ff !important;
}

.history-metrics-table {
  width: 100%;
  min-width: 0;
  margin-top: 8px;
  border-collapse: collapse;
  background: #fff;
  table-layout: fixed;
}

.history-metrics-table th,
.history-metrics-table td {
  border: 1px solid #e5eaf2;
  padding: 10px 12px;
  text-align: left;
}

.history-metrics-table th {
  width: 220px;
  background: #f5f7fb;
  color: #344054;
  font-weight: 500;
}

.evaluation-form label {
  grid-template-columns: 132px minmax(0, 430px);
  margin: 0;
}

.evaluation-choice-row {
  display: inline-flex;
  align-items: center;
  gap: 14px;
  min-width: 980px;
  white-space: nowrap;
}

.evaluation-choice-label {
  flex: 0 0 132px;
}

.evaluation-radio-group {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 18px;
  white-space: nowrap;
}

.evaluation-radio-group :deep(.el-radio) {
  display: inline-flex;
  align-items: center;
  margin-right: 0;
  white-space: nowrap;
}

.evaluation-radio-group :deep(.el-radio__label) {
  white-space: nowrap;
}

.evaluation-row {
  position: relative;
}

.evaluation-tip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #667085;
  font-style: normal;
  white-space: nowrap;
}

.help-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border: 1px solid #c0c8d2;
  border-radius: 999px;
  color: #98a2b3;
  font-size: 12px;
  line-height: 1;
}

.custom-dataset-picker {
  display: flex;
  align-items: center;
  gap: 18px;
  margin: 0 0 2px 132px;
}

.custom-dataset-picker button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: #1763ff;
  font-size: 14px;
  line-height: 1;
  padding: 0;
  transition:
    color 0.16s ease,
    opacity 0.16s ease;
}

.custom-dataset-picker button:hover,
.custom-dataset-picker button.active {
  color: #0f5cff;
  opacity: 0.86;
}

.custom-dataset-picker strong {
  font-weight: 400;
}

.custom-dataset-picker small {
  color: #8aa4d6;
  font-size: 12px;
}

.custom-dataset-picker button.active small {
  color: #1763ff;
}

.dataset-folder-icon {
  color: #8ab4ff;
}

.selected-evaluation-dataset {
  margin: -4px 0 0 132px;
  color: #667085;
  font-size: 13px;
}

.selected-evaluation-dataset strong {
  color: #111827;
  font-weight: 500;
}

.result-files-dialog :deep(.el-dialog__body) {
  min-height: 430px;
  padding: 8px 20px 24px;
}

.result-files-table {
  width: 100%;
}

.result-files-row {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(100px, 1fr) 84px;
  min-height: 54px;
  align-items: center;
  border-bottom: 1px solid #edf0f5;
  padding: 0 16px;
  color: #344054;
}

.result-files-header {
  min-height: 56px;
  background: #fafafa;
  color: #111827;
  font-weight: 600;
}

.result-files-row a {
  color: #1763ff;
  text-decoration: none;
}

.result-files-empty {
  margin: 72px 0 0;
  color: #98a2b3;
  text-align: center;
}

.evaluation-dataset-dialog :deep(.el-dialog__header) {
  margin-right: 0;
  padding: 18px 30px 12px;
}

.evaluation-dataset-dialog :deep(.el-dialog__title) {
  color: #111827;
  font-size: 20px;
  font-weight: 700;
}

.evaluation-dataset-dialog :deep(.el-dialog__body) {
  padding: 0 30px;
}

.evaluation-dataset-dialog :deep(.el-dialog__footer) {
  border-top: 1px solid #edf0f5;
  padding: 20px 30px;
}

.weight-mark-dialog :deep(.el-dialog__body) {
  padding: 30px 32px 0;
}

.weight-mark-dialog :deep(.el-dialog__footer) {
  padding: 24px 32px 28px;
}

.weight-mark-title {
  margin: 0 0 12px;
  color: #111827;
  font-size: 16px;
  line-height: 1.5;
}

.weight-mark-tip {
  margin: 8px 0 0;
  color: #98a2b3;
  font-size: 13px;
  line-height: 1.7;
}

.evaluation-dataset-tabs {
  display: flex;
  gap: 28px;
  border-bottom: 1px solid #edf0f5;
  margin-bottom: 18px;
}

.evaluation-dataset-tabs button {
  height: 48px;
  border: 0;
  border-bottom: 3px solid transparent;
  background: transparent;
  color: #667085;
  cursor: pointer;
  font-size: 15px;
  padding: 0;
}

.evaluation-dataset-tabs button.active {
  border-bottom-color: #1763ff;
  color: #1763ff;
  font-weight: 700;
}

.evaluation-dataset-list {
  max-height: 520px;
  min-height: 420px;
  overflow-y: auto;
  padding: 2px 0 16px;
}

.evaluation-dataset-row {
  display: grid;
  grid-template-columns: 18px 36px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 64px;
  border: 0;
  background: transparent;
  color: #344054;
  cursor: pointer;
  padding: 8px 12px;
  text-align: left;
}

.evaluation-dataset-row:hover {
  background: #f7faff;
}

.dataset-check {
  width: 16px;
  height: 16px;
  border: 1px solid #cfd8e6;
  border-radius: 2px;
  background: #fff;
}

.evaluation-dataset-row.selected .dataset-check {
  border-color: #1763ff;
  background:
    linear-gradient(45deg, transparent 46%, #fff 46% 56%, transparent 56%),
    linear-gradient(-45deg, transparent 42%, #fff 42% 52%, transparent 52%),
    #1763ff;
}

.dataset-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 4px;
  background: #2f7df6;
  color: #fff;
  font-size: 20px;
  font-weight: 700;
}

.dataset-name {
  overflow: hidden;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evaluation-dataset-empty {
  display: grid;
  min-height: 260px;
  place-items: center;
  color: #98a2b3;
}

@container model-space (max-width: 1400px) {
  .pipeline-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@container model-space (max-width: 1080px) {
  .pipeline-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@container model-space (max-width: 920px) {
  .list-toolbar,
  .page-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .toolbar-controls {
    flex-wrap: wrap;
    width: 100%;
  }

  .search-input {
    flex: 1 1 220px;
    width: auto;
  }
}

@container model-space (max-width: 720px) {
  .pipeline-grid,
  .scenario-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .data-step {
    grid-template-columns: 1fr;
  }

}

@container model-space (max-width: 480px) {
  .pipeline-grid,
  .scenario-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .header-actions,
  .toolbar-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar-select,
  .search-input {
    width: 100%;
  }
}
</style>
