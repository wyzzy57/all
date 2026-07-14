<template>
  <section class="model-space-view">
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
          <button :class="{ active: detailTab === 'deploy' }" type="button" @click="detailTab = 'deploy'">部署</button>
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
            <h3>请选择模型方案</h3>
            <p>请选择各模块对应的模型方案，默认选择官方提供的模型权重，支持用户修改为在产线评估环节标记的模型权重</p>

            <template v-if="deployMode === 'online'">
              <div class="model-radio-row required-row">
                <span>{{ taskLabel(detailPipeline.task) }}模块：</span>
                <el-radio-group v-model="deployForm.model">
                  <el-radio v-for="model in deployModelOptions" :key="model" :label="model">{{ model }}</el-radio>
                </el-radio-group>
              </div>
              <el-select v-model="deployForm.weight" class="detail-select" placeholder="请选择模型权重">
                <el-option
                  v-for="option in deployWeightOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
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
            <label class="deploy-field">
              <span class="required-label">选择环境：</span>
              <el-select v-model="deployForm.environment" placeholder="请选择环境">
                <el-option
                  v-for="environment in deploymentEnvironmentOptions"
                  :key="environment.value"
                  :label="environment.label"
                  :value="environment.value"
                />
              </el-select>
            </label>
            <div class="env-note">
              <strong>环境分配：</strong>
              <span>{{ selectedDeploymentEnvironment?.resource || "请先选择环境" }}</span>
              <template v-if="selectedDeploymentEnvironment">
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
      <header class="wizard-topbar">
        <button class="back-link" type="button" @click="backToList">
          <el-icon><ArrowLeft /></el-icon>
          返回产线列表
        </button>
        <nav class="wizard-steps" aria-label="??????">
          <button
            v-for="(step, index) in wizardSteps"
            :key="step"
            type="button"
            :class="{ active: activeStep === index, done: activeStep > index }"
            @click="activeStep = index"
          >
            <span>{{ index + 1 }}</span>
            <strong>{{ step }}</strong>
          </button>
        </nav>
      </header>

      <section class="wizard-panel">
        <div v-if="activeStep === 0" class="step-panel">
          <h2>选择产线</h2>
          <button class="scenario-card selected" type="button">
            <strong>{{ form.name || selectedScenario.label }}</strong>
            <span>{{ selectedScenario.description }}</span>
            <em>{{ taskLabel(form.task) }}</em>
            <a>在线体验</a>
          </button>
        </div>

        <div v-else-if="activeStep === 1" class="step-panel data-step">
          <div class="step-main">
            <h2>请 选择模型并添加数据集</h2>
            <label class="field-label required">选择模型</label>
            <el-select v-model="form.base_model_id" class="full-input" placeholder="请选择基础模型" @change="syncScaleFromModel">
              <el-option
                v-for="model in selectableBaseModels"
                :key="model.id"
                :label="modelLabel(model)"
                :value="model.id"
              />
            </el-select>

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
          <div class="params-header">
            <h2>请设置模型参数 <span>{{ selectedModelName }}</span></h2>
            <button type="button" class="link-button" @click="configMode = !configMode">
              {{ configMode ? "退出修改配置文件" : "修改配置文件" }}
            </button>
          </div>

          <textarea v-if="configMode" v-model="configText" class="config-editor" spellcheck="false" />

          <div v-else class="params-form">
            <label class="param-field">
              <span>* 轮次(Epochs)</span>
              <el-input-number v-model="form.epochs" :min="1" :max="10000" />
              <small>训练轮次越大，耗时越久，最终精度通常越高</small>
            </label>
            <label class="param-field">
              <span>* 批大小(Batch Size)</span>
              <el-input-number v-model="form.batch" :min="1" :max="1024" />
              <small>单卡Batch Size，值越大，显存占用越高</small>
            </label>
            <label class="param-field">
              <span>* 类别数量(Class Num)</span>
              <el-input-number v-model="form.class_num" :min="1" :max="10000" />
              <small>类别的数量，根据实际情况填写</small>
            </label>
            <label class="param-field">
              <span>* 学习率(Learning Rate)</span>
              <el-input v-model="form.lr0" />
              <small>学习率建议参考Batch Size进行同比例的调整</small>
            </label>

            <details class="advanced-config" open>
              <summary>高级配置</summary>
              <label class="param-field">
                <span>log打印间隔(Log Interval) / step</span>
                <el-input-number v-model="form.log_interval" :min="1" :max="100000" />
                <small>每隔多少个step打印一次log信息</small>
              </label>
              <label class="param-field">
                <span>断点训练权重</span>
                <el-select v-model="form.resume_weight" class="full-input" placeholder="请选择">
                  <el-option label="不使用断点权重" value="" />
                  <el-option label="上次训练checkpoint" value="last.pt" />
                </el-select>
                <small>从训练中断保存的checkpoint继续训练</small>
              </label>
              <label class="param-field">
                <span>预训练权重</span>
                <el-select v-model="form.pretrained" class="full-input">
                  <el-option label="官方权重.pt" value="official" />
                  <el-option label="不使用预训练权重" value="false" />
                </el-select>
                <small>从预训练的权重开始微调，提高训练效率</small>
              </label>
              <label class="param-field">
                <span>热启动步数（WarmUp Steps）</span>
                <el-input-number v-model="form.warmup_epochs" :min="0" :max="10000" :step="0.5" />
                <small>在训练初始阶段以较小学习率训练的step数</small>
              </label>
              <label class="param-field">
                <span>保存间隔(Save Interval) / epoch</span>
                <el-input-number v-model="form.save_period" :min="-1" :max="10000" />
                <small>每隔多少个epoch进行一次模型保存</small>
              </label>
              <label class="param-field">
                <span>评估、保存间隔(Eval Interval) / epoch</span>
                <el-input-number v-model="form.eval_interval" :min="1" :max="10000" />
                <small>每隔多少个epoch进行一次模型评估及模型保存</small>
              </label>
            </details>
          </div>
        </div>

        <div v-else class="step-panel submit-step">
          <h2>请选择训练环境</h2>
          <label class="field-label required">选择环境</label>
          <el-select v-model="form.device" class="environment-select" placeholder="请选择训练环境">
            <el-option label="CPU" value="cpu" />
            <el-option label="gpu节点_1" value="0" />
          </el-select>
          <div class="submit-summary">
            <span>产线名称：{{ form.name }}</span>
            <span>任务类型：{{ taskLabel(form.task) }}</span>
            <span>基础模型：{{ selectedModelName }}</span>
            <span>数据集：{{ selectedDataset?.name || "待选择" }}</span>
          </div>
        </div>

        <footer class="wizard-footer">
          <el-button v-if="activeStep > 0" plain @click="activeStep -= 1">上一步</el-button>
          <el-button v-if="activeStep === 0" plain>直接部署</el-button>
          <el-button v-if="activeStep < 3" type="primary" @click="goNext">下一步</el-button>
          <el-button v-else type="primary" :loading="submitting" @click="submitTraining">提交训练</el-button>
        </footer>
      </section>
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

    <el-dialog v-model="createDialogVisible" title="创建产线" width="720px" class="create-dialog">
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
            <strong>{{ scenario.label }}</strong>
            <span>{{ scenario.description }}</span>
          </button>
        </div>
      </div>

      <div v-else class="create-form local-form">
        <label class="field-label required">产线名称</label>
        <el-input v-model="createForm.name" placeholder="请输入产线名称" />
        <label class="field-label required">文件上传</label>
        <button class="upload-box" type="button">
          <el-icon><UploadFilled /></el-icon>
          <span>点击上传本地模型文件</span>
        </button>
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

    <el-dialog v-model="publicDialogVisible" title="公开配置" width="560px" class="public-dialog">
      <div class="public-switch-row">
        <div>
          <strong>是否公开</strong>
          <p>数据集公开后可被其他用户访问</p>
        </div>
        <el-switch v-model="publicForm.is_public" />
      </div>
      <div class="public-section-title">* 公开权限设置 <span>已选：{{ selectedScopes.length }}</span></div>
      <div class="permission-grid">
        <el-checkbox :model-value="allScopesSelected" @change="toggleAllScopes">全选</el-checkbox>
        <el-checkbox
          v-for="scope in scopeOptions"
          :key="scope"
          :model-value="selectedScopes.includes(scope)"
          @change="toggleScope(scope)"
        >
          {{ scope }}
        </el-checkbox>
      </div>
      <template #footer>
        <el-button type="primary" @click="confirmPublicConfig">配置完成</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import {
  ArrowLeft,
  Check,
  CircleClose,
  Download,
  Loading,
  MoreFilled,
  Refresh,
  Search,
  Star,
  StarFilled,
  UploadFilled,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

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
} from "@/api/client";
import type { ExperienceInferenceRequest } from "@/components/ServiceExperiencePanel.vue";
import ServiceExperiencePanel from "@/components/ServiceExperiencePanel.vue";

type ActiveTab = "all" | "mine" | "favorite";
type ViewMode = "list" | "wizard" | "detail";
type CreateTab = "zero" | "local";
type ProcessingTab = "train" | "val" | "test" | "classes";
type DetailTab = "basic" | "logs" | "experience" | "deploy" | "evaluate";

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
};

type WeightOption = {
  label: string;
  value: string;
  modelId?: string;
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
  device: string;
};

const router = useRouter();

const tabOptions: Array<{ label: string; value: ActiveTab }> = [
  { label: "全部产线", value: "all" },
  { label: "我创建的", value: "mine" },
  { label: "我收藏的", value: "favorite" },
];

const scenarios: Scenario[] = [
  { key: "detect", label: "目标检测", task: "detect", description: "通用目标检测" },
  { key: "doc", label: "文档图像信息抽取", task: "document", description: "版面理解与字段抽取" },
  { key: "ocr", label: "OCR", task: "ocr", description: "文本检测与识别" },
  { key: "table", label: "通用表格识别", task: "table", description: "表格结构识别" },
  { key: "classify", label: "图像分类", task: "classify", description: "单标签与多标签分类" },
  { key: "timeseries", label: "时序分析", task: "timeseries", description: "时序预测与异常分析" },
  { key: "segment", label: "图像分割", task: "segment", description: "实例分割" },
  { key: "attribute", label: "属性识别", task: "attribute", description: "属性标签识别" },
  { key: "llm", label: "大模型训练", task: "llm", description: "偏好对齐与微调" },
];

const scopeOptions = ["管理员部门", "1组", "2组", "班级1", "班级2"];

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
const detailTab = ref<DetailTab>("basic");
const deployMode = ref<"online" | "offline">("online");
const evaluationTab = ref<"pipeline" | "history">("pipeline");
const batchMode = ref(false);
const createDialogVisible = ref(false);
const renameDialogVisible = ref(false);
const publicDialogVisible = ref(false);
const resultFilesDialogVisible = ref(false);
const resultFilesLoading = ref(false);
const resultFiles = ref<TrainingArtifactRecord[]>([]);
const deploying = ref(false);
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
const selectedScopes = ref<string[]>([]);
const configParams = ref<Record<string, unknown>>({});
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
});

const publicForm = reactive({
  is_public: false,
});

const deployForm = reactive({
  serviceName: "",
  model: "",
  weight: "official",
  environment: "",
  instanceName: "",
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
  device: "cpu",
});

const typeOptions = computed(() => Array.from(new Set(pipelines.value.map((item) => item.task))).sort());

const selectedScenario = computed(
  () => scenarios.find((scenario) => scenario.key === createForm.scenarioKey) || scenarios[0],
);

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
  return model ? modelLabel(model) : "YOLO26";
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
const deployModelOptions = computed(() => {
  const pipeline = detailPipeline.value;
  const models = baseModels.value.filter((model) => !pipeline || model.task === pipeline.task).map((model) => modelLabel(model));
  return models.length > 0 ? models.slice(0, 6) : [detailModelName.value === "-" ? "YOLO26" : detailModelName.value];
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
      deploymentName,
    });
  });
  return options.sort((left, right) => weightOrder(left.value) - weightOrder(right.value) || left.value.localeCompare(right.value));
});
const detailInferenceModelOptions = computed<WeightOption[]>(() => {
  return [...trainedWeightOptions.value, { label: "官方/基础权重", value: "base" }];
});
const inferenceEnvironmentOptions = ["cpu", "0", "gpu-node-1", "gpu-node-2"];
const deploymentEnvironmentOptions = [
  { value: "cpu", label: "CPU", resource: "CPU 共享资源" },
  { value: "gpu-node-1", label: "gpu节点_1", resource: "gpu节点_1 显卡1（3698.5M/12288.0M）" },
  { value: "gpu-node-2", label: "gpu节点_2", resource: "gpu节点_2 显卡0（8192.0M/24576.0M）" },
];
const selectedDeploymentEnvironment = computed(() =>
  deploymentEnvironmentOptions.find((environment) => environment.value === deployForm.environment),
);
const canDeploy = computed(() =>
  Boolean(
    deployForm.serviceName.trim() &&
      deployForm.model &&
      deployForm.weight &&
      deployForm.environment &&
      deployForm.instanceName.trim(),
  ),
);
const detailWeightOptions = computed(() => {
  const options = detailInferenceModelOptions.value.filter((option) => option.value !== "base");
  return options.length > 0 ? options : [{ label: "best.pt", value: "best.pt" }];
});
const deployWeightOptions = computed(() => [
  { label: "官方预训练模型", value: "official" },
  ...detailWeightOptions.value,
]);
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
const allScopesSelected = computed(() => selectedScopes.value.length === scopeOptions.length);

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

watch(configMode, (enabled) => {
  if (enabled) configText.value = buildConfigText();
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
  void loadWorkspace();
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
  createDialogVisible.value = true;
  createTab.value = "zero";
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
}

async function startWizard() {
  const scenario = selectedScenario.value;
  const name = createForm.name.trim();
  if (!name) {
    ElMessage.warning("请输入产线名称");
    return;
  }
  form.name = name;
  form.task = scenario.task;
  form.scale = "n";
  form.base_model_id = "";
  form.dataset_id = "";
  analysisResult.value = null;
  samplesBySplit.value = { train: [], val: [], test: [] };
  activeSampleId.value = "";
  processingTab.value = "train";
  configParams.value = {};
  ensureWizardDefaults();
  submitting.value = true;
  try {
    const pipeline = await api.createPipeline({
      name: form.name,
      task: form.task,
      scale: form.scale,
    });
    wizardPipelineId.value = pipeline.id;
    pipelines.value = [pipeline, ...pipelines.value.filter((item) => item.id !== pipeline.id)];
    activeStep.value = 0;
    createDialogVisible.value = false;
    viewMode.value = "wizard";
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
  applyPipelineParams(pipeline);
  configParams.value = normalizeConfigParams(pipeline.params_template ?? {});
  analysisResult.value = null;
  samplesBySplit.value = { train: [], val: [], test: [] };
  activeSampleId.value = "";
  processingTab.value = "train";
  ensureWizardDefaults();
  activeStep.value = 0;
  viewMode.value = "wizard";
  if (form.dataset_id) await loadWizardProcessingData();
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
  deployForm.model = detailModelName.value === "-" ? "" : detailModelName.value;
  deployForm.weight = "official";
  deployForm.environment = "";
  deployForm.instanceName = "";
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
  if (mode === "offline" && (deployForm.weight === "official" || !deployForm.weight)) {
    deployForm.weight = detailWeightOptions.value[0]?.value ?? "";
  }
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
  const environment = selectedDeploymentEnvironment.value;
  if (!pipeline || !environment || !canDeploy.value) {
    ElMessage.warning("请完整填写部署配置");
    return;
  }
  const selectedWeight = deployWeightOptions.value.find((option) => option.value === deployForm.weight);
  deploying.value = true;
  try {
    await api.createService({
      name: deployForm.serviceName.trim(),
      pipeline_id: pipeline.id,
      ...(selectedWeight?.modelId ? { trained_model_id: selectedWeight.modelId } : {}),
      model_name: deployForm.model,
      model_weight: deployForm.weight,
      environment: deployForm.environment,
      instance_name: deployForm.instanceName.trim(),
      resource_summary: environment.resource,
      config: { task: pipeline.task, pipeline_name: pipeline.name },
    });
    ElMessage.success("服务部署成功");
    await router.push("/services");
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "服务部署失败"));
  } finally {
    deploying.value = false;
  }
}

function openEvaluationSubtab(tab: "pipeline" | "history") {
  evaluationTab.value = tab;
  void loadEvaluationHistory();
}

function backToList() {
  viewMode.value = "list";
  activeStep.value = 0;
  detailPipelineId.value = "";
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

async function goNext() {
  if (activeStep.value === 0) {
    ensureWizardDefaults();
    activeStep.value += 1;
    await loadWizardProcessingData();
    return;
  }
  if (activeStep.value === 1) {
    if (!form.base_model_id) {
      ElMessage.warning("请选择基础模型");
      return;
    }
    if (!form.dataset_id) {
      ElMessage.warning("请选择数据集");
      return;
    }
    await updateClassCountFromDataset();
  }
  if (activeStep.value === 2 && configMode.value) {
    applyConfigText();
  }
  activeStep.value += 1;
}

function ensureWizardDefaults() {
  if (!form.base_model_id) {
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
  if (!form.base_model_id || !form.dataset_id) {
    ElMessage.warning("请先选择模型和数据集");
    return;
  }
  if (configMode.value) applyConfigText();
  submitting.value = true;
  try {
    if (wizardPipelineId.value) {
      await api.updatePipeline(wizardPipelineId.value, {
        task: form.task,
        scale: form.scale,
        base_model_id: form.base_model_id,
        dataset_id: form.dataset_id,
        params_template: trainingParams(),
        default_environment: { device: form.device, workers: 2 },
      });
      await api.createTrainingJob(wizardPipelineId.value, {});
      ElMessage.success("已提交训练");
      await loadWorkspace();
      backToList();
      return;
    }
    const pipeline = await api.createPipeline({
      name: form.name,
      task: form.task,
      scale: form.scale,
      base_model_id: form.base_model_id,
      dataset_id: form.dataset_id,
      params_template: trainingParams(),
      default_environment: { device: form.device, workers: 2 },
    });
    await api.createTrainingJob(pipeline.id, {});
    ElMessage.success("已提交训练");
    await loadWorkspace();
    backToList();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "提交训练失败"));
  } finally {
    submitting.value = false;
  }
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

function applyPipelineParams(pipeline: TrainingPipelineRecord) {
  const params = pipeline.params_template ?? {};
  const environment = pipeline.default_environment ?? {};
  form.epochs = numberParam(params.epochs, form.epochs);
  form.batch = numberParam(params.batch, form.batch);
  form.lr0 = stringParam(params.lr0, form.lr0);
  form.warmup_epochs = numberParam(params.warmup_epochs ?? params.warmup_steps, form.warmup_epochs);
  form.save_period = numberParam(params.save_period, form.save_period);
  form.pretrained = params.pretrained === false ? "false" : "official";
  form.resume_weight = params.resume ? "last.pt" : "";
  if (typeof environment.device === "string" || typeof environment.device === "number") {
    form.device = String(environment.device);
  }
}

function buildConfigText() {
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
  const lines = configText.value.split(/\r?\n/);
  const next: Record<string, unknown> = {};
  for (const line of lines) {
    const match = line.match(/^([a-zA-Z0-9_]+):\s*(.*)$/);
    if (!match) continue;
    const [, key, rawValue] = match;
    const paramKey = key === "warmup_steps" ? "warmup_epochs" : key;
    if (!yoloParamKeys.includes(paramKey)) continue;
    const value = rawValue.trim();
    const parsed = parseConfigValue(value);
    if (parsed !== undefined) next[paramKey] = parsed;
  }
  configParams.value = normalizeConfigParams(next);
  syncCommonFieldsFromConfig(configParams.value);
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
  editingPipelineId.value = pipeline.id;
  publicForm.is_public = Boolean(pipeline.is_public);
  const scope = pipeline.public_scope as { scopes?: unknown } | undefined;
  selectedScopes.value = Array.isArray(scope?.scopes) ? scope.scopes.map(String) : [];
  publicDialogVisible.value = true;
}

async function confirmPublicConfig() {
  if (!editingPipelineId.value) return;
  try {
    const updated = await api.updatePipeline(editingPipelineId.value, {
      is_public: publicForm.is_public,
      public_scope: { scopes: selectedScopes.value },
    });
    replacePipeline(updated);
    publicDialogVisible.value = false;
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "公开配置保存失败"));
  }
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

function toggleScope(scope: string) {
  selectedScopes.value = selectedScopes.value.includes(scope)
    ? selectedScopes.value.filter((item) => item !== scope)
    : [...selectedScopes.value, scope];
}

function toggleAllScopes() {
  selectedScopes.value = allScopesSelected.value ? [] : [...scopeOptions];
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

function parseConfigValue(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed || trimmed === "null") return undefined;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (/^\[[\d\s,.-]*\]$/.test(trimmed)) {
    const items = trimmed.slice(1, -1).split(",").map((item) => item.trim()).filter(Boolean).map(Number);
    return items.every(Number.isFinite) ? items : undefined;
  }
  const numeric = Number(trimmed);
  if (Number.isFinite(numeric)) return numeric;
  return trimmed.replace(/^["']|["']$/g, "");
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
  min-height: 100%;
  color: #111827;
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
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 2px 6px rgb(17 24 39 / 3%);
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
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 18px;
  padding: 20px 0 4px;
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
  gap: 8px;
  margin-bottom: 24px;
}

.scenario-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  max-height: 360px;
  overflow: auto;
}

.scenario-option {
  display: grid;
  gap: 10px;
  min-height: 104px;
  padding: 16px;
}

.scenario-option span {
  color: #667085;
  font-size: 13px;
}

.upload-box {
  display: grid;
  width: 100%;
  height: 120px;
  place-items: center;
  border: 1px dashed #b9c3d4;
  background: #fbfcff;
  color: #667085;
  cursor: pointer;
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

.model-radio-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 24px 0 14px 90px;
}

.model-radio-row > span {
  min-width: 114px;
  text-align: right;
}

.detail-select {
  width: 540px;
  margin-left: 214px;
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

@media (max-width: 1400px) {
  .pipeline-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@media (max-width: 1100px) {
  .pipeline-grid,
  .scenario-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .data-step {
    grid-template-columns: 1fr;
  }

  .list-toolbar,
  .page-header {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
