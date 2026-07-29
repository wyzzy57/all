<template>
  <section class="llm-wizard" data-testid="llm-pipeline-wizard">
    <div v-if="activeStep === 0" class="llm-step llm-overview-step">
      <header class="step-heading">
        <div><h2>选择产线</h2><p>确认产线名称和当前大模型训练能力。</p></div>
        <span class="draft-state">草稿自动保存</span>
      </header>

      <label class="llm-field llm-name-field">
        <span class="required-label">产线名称</span>
        <el-input v-model="form.name" maxlength="64" show-word-limit placeholder="请输入产线名称" />
      </label>

      <div class="llm-scenario" aria-label="已选择大模型训练">
        <span class="scenario-icon"><el-icon><Cpu /></el-icon></span>
        <div>
          <div class="scenario-title"><strong>大模型训练</strong><em>监督微调 SFT</em></div>
          <p>使用 LoRA 或 QLoRA 对文本大模型进行领域微调。</p>
          <small>当前版本：文本模型 · 单机单卡 NVIDIA GPU</small>
        </div>
      </div>
    </div>

    <div v-else-if="activeStep === 1" class="llm-step">
      <header class="step-heading">
        <div><h2>模型与数据</h2><p>先确定基础模型，再使用对应 tokenizer 检查训练数据。</p></div>
      </header>

      <section class="llm-section" aria-labelledby="llm-base-model-title">
        <div class="section-title"><h3 id="llm-base-model-title">基础模型</h3><span>边缘节点下载并缓存</span></div>
        <div class="source-switch" role="group" aria-label="模型来源">
          <button v-for="source in modelSources" :key="source.value" type="button" :class="{ active: form.modelSource === source.value }" @click="selectModelSource(source.value)">
            {{ source.label }}
          </button>
        </div>
        <div class="model-entry-grid">
          <label class="llm-field model-id-field">
            <span class="required-label">模型 ID</span>
            <el-select
              v-model="form.modelId"
              data-testid="llm-model-id-selector"
              class="model-id-select"
              filterable
              allow-create
              default-first-option
              clearable
              placeholder="选择推荐模型或输入 namespace/repository"
              @change="invalidateModelResolution"
            >
              <el-option v-for="model in recommendedModels" :key="model.id" :label="model.name" :value="model.id">
                <span class="model-option"><strong>{{ model.name }}</strong><small>{{ model.id }}</small><em>{{ model.parameterScale }} · {{ model.suitability }}</em></span>
              </el-option>
            </el-select>
            <small>可从推荐列表选择，也可输入其他模型仓库 ID；不填写本机文件路径。</small>
          </label>
          <label class="llm-field">
            <span>版本 Revision</span>
            <el-input v-model="form.requestedRevision" placeholder="main、tag 或 commit" @input="modelReferenceChecked = false" />
            <small>训练记录会保存最终解析出的不可变 commit。</small>
          </label>
          <button class="resolve-button" type="button" :disabled="!form.modelId.trim() || modelResolving" @click="checkModelReference">
            <el-icon><Search /></el-icon>{{ modelResolving ? "正在解析" : "解析模型" }}
          </button>
        </div>
        <div v-if="modelReferenceChecked" class="model-resolution" role="status">
          <el-icon><CircleCheck /></el-icon>
          <div><strong>模型仓库解析成功</strong><span>{{ form.modelId }} @ {{ form.resolvedRevision }}</span></div>
          <small>{{ modelResolution?.pipeline_tag || "任务类型待识别" }} · {{ modelResolution?.license || "未声明许可证" }}</small>
        </div>
        <div v-else-if="modelResolutionError" class="model-resolution error" role="alert">
          <el-icon><WarningFilled /></el-icon><span>{{ modelResolutionError }}</span>
        </div>
        <details class="inline-advanced">
          <summary>模型高级设置</summary>
          <div class="advanced-inline-grid">
            <label class="llm-field">
              <span>对话模板</span>
              <el-input v-model="form.template" placeholder="auto" />
              <small>默认自动识别，仅在识别不正确时覆盖。</small>
            </label>
            <label class="risk-switch">
              <span><strong>允许远程模型代码</strong><small>仅对白名单模型启用，默认关闭。</small></span>
              <el-switch v-model="form.trustRemoteCode" />
            </label>
          </div>
        </details>
      </section>

      <section class="llm-section" aria-labelledby="llm-dataset-title">
        <div class="section-title">
          <h3 id="llm-dataset-title">训练数据集</h3>
          <button v-if="selectedDataset" data-testid="llm-dataset-details" class="section-action" type="button" @click="datasetDetailsVisible = true">查看详情</button>
          <span v-else>仅显示已校验的 SFT 数据集</span>
        </div>
        <label class="llm-field dataset-field">
          <span class="required-label">数据集</span>
          <el-select v-model="form.datasetId" placeholder="请选择大模型数据集" class="full-width">
            <el-option v-for="dataset in llmDatasets" :key="dataset.id" :label="dataset.name" :value="dataset.id" />
          </el-select>
        </label>
        <div class="dataset-actions">
          <input ref="datasetFileInput" class="visually-hidden" type="file" accept=".json,.jsonl,application/json,application/x-ndjson" @change="uploadDatasetFile" />
          <button class="dataset-upload-button" type="button" :disabled="datasetUploading" @click="datasetFileInput?.click()">
            <el-icon><Upload /></el-icon>{{ datasetUploading ? "正在校验并上传" : "上传 SFT 数据集" }}
          </button>
          <span>支持 Alpaca、ShareGPT、OpenAI messages 的 JSON / JSONL</span>
        </div>
        <div v-if="datasetUploadSummary" class="upload-summary" role="status">
          <strong>已导入 {{ datasetUploadSummary.valid_count }} 条有效样本</strong>
          <span v-if="datasetUploadSummary.invalid_count">忽略 {{ datasetUploadSummary.invalid_count }} 条无效样本</span>
        </div>
        <div v-if="selectedDataset" class="dataset-summary">
          <div><span>数据格式</span><strong>{{ datasetFormat }}</strong></div>
          <div><span>样本数量</span><strong>{{ selectedDataset.sample_count }}</strong></div>
          <div><span>有效标注</span><strong>{{ selectedDataset.annotation_count }}</strong></div>
          <div><span>校验状态</span><strong class="success-text">已通过</strong></div>
        </div>
        <section v-if="selectedDataset && datasetPreview" class="token-analysis" aria-labelledby="token-distribution-title">
          <div class="preview-heading">
            <strong id="token-distribution-title">Token 长度分布</strong>
            <span>P50 {{ tokenP50 }} · P95 {{ tokenP95 }} · 最大 {{ datasetPreview.token_analysis.maximum }}</span>
          </div>
          <div class="token-bars" role="img" :aria-label="`Token 长度分布，P50 ${tokenP50}，P95 ${tokenP95}`">
            <i v-for="(height, index) in tokenBarHeights" :key="index" :style="{ height: `${height}%` }" />
            <b class="cutoff-marker" :style="{ left: `${cutoffMarkerPercent}%` }"><span>截断 {{ form.cutoffLen }}</span></b>
          </div>
          <p>当前截断长度 {{ form.cutoffLen }}，预计 {{ truncationPercent }}% 样本会被截断。</p>
        </section>
        <div v-if="selectedDataset" class="conversation-preview">
          <div class="preview-heading"><strong>对话预览</strong><span v-if="datasetPreview">样本估算 {{ datasetPreview.token_analysis.average }} tokens，范围 {{ datasetPreview.token_analysis.minimum }}-{{ datasetPreview.token_analysis.maximum }}</span></div>
          <p v-if="datasetPreviewLoading" class="preview-state">正在读取数据样本...</p>
          <p v-else-if="datasetPreviewError" class="preview-state error">
            {{ datasetPreviewError }}
            <button type="button" @click="loadDatasetPreview(selectedDataset.id)">重试</button>
          </p>
          <template v-else-if="datasetPreview?.samples[0]">
            <div v-for="(message, index) in datasetPreview.samples[0].messages" :key="`${message.role}-${index}`" class="message-row" :class="{ assistant: message.role === 'assistant' }">
              <em>{{ message.role }}</em><p>{{ message.content }}</p>
            </div>
          </template>
          <p v-else class="preview-state">当前数据集没有可预览样本</p>
        </div>
        <div v-else class="dataset-empty"><el-icon><Document /></el-icon><span>选择数据集后展示格式、对话样本和 Token 长度分析</span></div>
      </section>

      <div v-if="datasetDetailsVisible && selectedDataset" class="dataset-detail-backdrop" @click.self="datasetDetailsVisible = false">
        <aside class="dataset-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="dataset-detail-title">
          <header><div><h3 id="dataset-detail-title">{{ selectedDataset.name }}</h3><p>数据集详情与校验报告</p></div><button type="button" aria-label="关闭" @click="datasetDetailsVisible = false">×</button></header>
          <section><h4>字段映射</h4><dl><template v-for="(value, key) in selectedDataset.schema_config || {}" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl><p v-if="!Object.keys(selectedDataset.schema_config || {}).length">当前格式使用系统默认字段映射。</p></section>
          <section><h4>样本预览</h4><div v-for="sample in datasetPreview?.samples || []" :key="sample.index" class="drawer-sample"><strong>样本 {{ sample.index }} · {{ sample.token_estimate }} tokens</strong><span>{{ sample.messages.map((message) => `${message.role}: ${message.content}`).join(" · ") }}</span></div></section>
          <section><h4>校验报告</h4><p>格式 {{ datasetFormat }} · {{ selectedDataset.sample_count }} 个样本 · {{ selectedDataset.annotation_count }} 个有效训练响应</p><ul v-if="datasetUploadSummary?.issues.length"><li v-for="issue in datasetUploadSummary.issues" :key="`${issue.index}-${issue.code}`">第 {{ issue.index }} 条：{{ issue.message }}</li></ul><p v-else class="success-text">未发现阻断训练的问题。</p></section>
        </aside>
      </div>

      <div class="compatibility-result" :class="{ ready: dataStepReady }">
        <el-icon><CircleCheck v-if="dataStepReady" /><InfoFilled v-else /></el-icon>
        <span>{{ dataStepReady ? "模型引用与数据集已选择，可进入参数配置" : "请完成模型解析并选择已校验的数据集" }}</span>
      </div>
    </div>

    <div v-else-if="activeStep === 2" class="llm-step">
      <header class="step-heading parameter-heading">
        <div><h2>参数准备</h2><p>常用参数直接配置，完整配置可通过 YAML 修改。</p></div>
        <div class="config-mode-switch" role="group" aria-label="参数配置模式">
          <button type="button" :class="{ active: configMode === 'form' }" @click="switchConfigMode('form')">表单配置</button>
          <button type="button" :class="{ active: configMode === 'yaml' }" @click="switchConfigMode('yaml')">YAML 配置</button>
        </div>
      </header>

      <template v-if="configMode === 'form'">
        <section class="method-section">
          <div class="section-title"><h3>微调方案</h3><span>根据显存自动推荐</span></div>
          <div class="method-options">
            <button type="button" :class="{ selected: form.method === 'lora' }" @click="selectMethod('lora')"><strong>LoRA</strong><span>精度优先，显存占用较高</span></button>
            <button type="button" :class="{ selected: form.method === 'qlora' }" @click="selectMethod('qlora')"><strong>QLoRA</strong><em>推荐</em><span>4-bit 量化，适合 12 GB GPU</span></button>
          </div>
          <div class="recommendation"><el-icon><MagicStick /></el-icon><span>当前使用 {{ form.method === "qlora" ? "QLoRA 4-bit" : "LoRA" }}，有效批量 {{ effectiveBatchSize }}。</span></div>
        </section>

        <section class="llm-section parameter-section">
          <div class="section-title"><h3>基础配置</h3><span>训练阶段固定为 Supervised Fine-Tuning</span></div>
          <div class="parameter-grid">
            <label class="llm-field"><span>训练阶段</span><el-input model-value="Supervised Fine-Tuning" disabled /></label>
            <label class="llm-field"><span>学习率</span><el-input-number v-model="form.learningRate" :min="0.000001" :max="0.01" :step="0.00001" :precision="6" /></label>
            <label class="llm-field"><span>训练轮数</span><el-input-number v-model="form.epochs" :min="1" :max="100" :step="1" /></label>
            <label class="llm-field"><span>截断长度</span><el-input-number v-model="form.cutoffLen" :min="128" :max="131072" :step="128" /></label>
            <label class="llm-field"><span>单卡批量</span><el-input-number v-model="form.batchSize" :min="1" :max="64" /></label>
            <label class="llm-field"><span>梯度累积</span><el-input-number v-model="form.gradientAccumulationSteps" :min="1" :max="1024" /></label>
            <label class="llm-field"><span>验证集比例</span><el-input-number v-model="form.valSize" :min="0" :max="0.5" :step="0.05" :precision="2" /></label>
            <label class="llm-field"><span>学习率调度器</span><el-select v-model="form.scheduler"><el-option label="Cosine" value="cosine" /><el-option label="Linear" value="linear" /><el-option label="Constant" value="constant" /></el-select></label>
            <label class="llm-field"><span>热身比例</span><el-input-number v-model="form.warmupRatio" :min="0" :max="0.5" :step="0.01" :precision="2" /></label>
            <label class="llm-field"><span>计算精度</span><el-select v-model="form.precision"><el-option label="自动选择" value="auto" /><el-option label="BF16" value="bf16" /><el-option label="FP16" value="fp16" /></el-select></label>
          </div>
        </section>

        <section class="advanced-groups">
          <details open>
            <summary><span>LoRA 配置<small>rank、alpha、dropout 与目标层</small></span><span class="advanced-meta"><em>已修改 {{ advancedChangeCount("lora") }} 项</em><button type="button" @click.prevent.stop="resetAdvancedGroup('lora')">恢复推荐值</button></span></summary>
            <div class="parameter-grid compact-grid">
              <label class="llm-field"><span>LoRA Rank</span><el-input-number v-model="form.loraRank" :min="1" :max="1024" /></label>
              <label class="llm-field"><span>LoRA Alpha</span><el-input-number v-model="form.loraAlpha" :min="1" :max="2048" /></label>
              <label class="llm-field"><span>LoRA Dropout</span><el-input-number v-model="form.loraDropout" :min="0" :max="1" :step="0.05" :precision="2" /></label>
              <label class="llm-field"><span>目标层</span><el-input v-model="form.loraTarget" /></label>
            </div>
          </details>
          <details>
            <summary><span>训练稳定性<small>梯度、随机种子与显存优化</small></span><span class="advanced-meta"><em>已修改 {{ advancedChangeCount("stability") }} 项</em><button type="button" @click.prevent.stop="resetAdvancedGroup('stability')">恢复推荐值</button></span></summary>
            <div class="parameter-grid compact-grid stability-grid">
              <label class="llm-field"><span>最大梯度范数</span><el-input-number v-model="form.maxGradNorm" :min="0" :max="100" :step="0.1" /></label>
              <label class="llm-field"><span>随机种子</span><el-input-number v-model="form.seed" :min="0" :max="2147483647" /></label>
              <label class="llm-field"><span>Flash Attention</span><el-select v-model="form.flashAttention"><el-option label="自动" value="auto" /><el-option label="关闭" value="disabled" /></el-select></label>
              <label class="llm-field"><span>RoPE 插值</span><el-select v-model="form.ropeScaling"><el-option label="关闭" value="none" /><el-option label="Linear" value="linear" /><el-option label="Dynamic" value="dynamic" /></el-select></label>
              <label class="gradient-checkpoint-field gradient-checkpoint-field--full-row">
                <span class="gradient-checkpoint-field__header"><strong>梯度检查点</strong><el-switch v-model="form.gradientCheckpointing" /></span>
                <small>降低显存占用</small>
              </label>
            </div>
          </details>
          <details>
            <summary><span>日志与保存<small>指标、评估与检查点频率</small></span><span class="advanced-meta"><em>已修改 {{ advancedChangeCount("logging") }} 项</em><button type="button" @click.prevent.stop="resetAdvancedGroup('logging')">恢复推荐值</button></span></summary>
            <div class="parameter-grid compact-grid">
              <label class="llm-field"><span>日志间隔 / step</span><el-input-number v-model="form.loggingSteps" :min="1" /></label>
              <label class="llm-field"><span>评估间隔 / step</span><el-input-number v-model="form.evalSteps" :min="1" /></label>
              <label class="llm-field"><span>保存间隔 / step</span><el-input-number v-model="form.saveSteps" :min="1" /></label>
              <label class="llm-field"><span>最多保留检查点</span><el-input-number v-model="form.saveTotalLimit" :min="1" /></label>
            </div>
          </details>
          <details>
            <summary><span>数据性能<small>样本上限、Packing 与 Worker</small></span><span class="advanced-meta"><em>已修改 {{ advancedChangeCount("data") }} 项</em><button type="button" @click.prevent.stop="resetAdvancedGroup('data')">恢复推荐值</button></span></summary>
            <div class="parameter-grid compact-grid">
              <label class="llm-field"><span>最大样本数</span><el-input-number v-model="form.maxSamples" :min="1" placeholder="不限制" /></label>
              <label class="llm-field"><span>预处理 Worker</span><el-input-number v-model="form.preprocessingWorkers" :min="1" :max="64" /></label>
              <label class="llm-field"><span>DataLoader Worker</span><el-input-number v-model="form.dataloaderWorkers" :min="0" :max="64" /></label>
              <label class="packing-field packing-field--full-row">
                <span class="packing-field__header"><strong>样本 Packing</strong><el-switch v-model="form.packing" /></span>
                <small>拼接短样本提高吞吐</small>
              </label>
            </div>
          </details>
        </section>
      </template>

      <section v-else class="yaml-layout">
        <div class="yaml-main">
          <div class="yaml-toolbar"><span>完整 LLaMA-Factory 用户配置</span><div><button type="button" @click="copyYamlConfig">复制配置</button><button type="button" @click="resetRecommendedConfig">恢复推荐配置</button></div></div>
          <textarea v-model="yamlText" class="yaml-editor" spellcheck="false" aria-label="LLaMA-Factory YAML 配置" />
          <div class="yaml-status" :class="{ error: yamlError }"><el-icon><CircleCheck v-if="!yamlError" /><WarningFilled v-else /></el-icon><span>{{ yamlError || "配置语法有效，切回表单时会同步参数" }}</span></div>
        </div>
        <aside class="managed-fields"><h3>系统托管参数</h3><p>以下字段由 VisiOX 在提交时生成。</p><code v-for="field in managedLlmFields" :key="field">{{ field }}</code></aside>
      </section>
    </div>

    <div v-else class="llm-step">
      <header class="step-heading"><div><h2>提交训练</h2><p>选择运行节点并完成模型、数据、参数和资源预检。</p></div></header>
      <div class="resource-preflight-layout">
        <section class="llm-section resource-section">
          <div class="section-title"><h3>训练资源</h3><span>第一版固定使用单机单卡 NVIDIA GPU</span></div>
          <label class="llm-field">
            <span class="required-label">资源池</span>
            <el-select v-model="form.poolId" class="full-width" placeholder="请选择 GPU 资源池" @change="clearNodeSelection">
              <el-option v-for="pool in gpuResourcePools" :key="pool.id" :label="pool.name" :value="pool.id" />
            </el-select>
          </label>
          <p v-if="resourcesLoading" class="resource-message">正在读取远程 GPU 资源...</p>
          <p v-else-if="resourceError" class="resource-message error">{{ resourceError }}</p>
          <div v-else class="node-options">
            <button v-for="node in availableNodes" :key="node.id" type="button" :class="{ selected: form.nodeId === node.id }" @click="form.nodeId = node.id">
              <span class="node-title"><i></i><strong>{{ node.name }}</strong><em>在线</em></span>
              <span>{{ nodeGpuName(node) }} · {{ nodeGpuMemory(node) }}</span>
              <small>{{ node.architecture }} · {{ node.platform_kind }} · {{ nodeLastChecked(node) }}</small>
            </button>
          </div>
          <div v-if="!resourcesLoading && !resourceError && form.poolId && availableNodes.length === 0" class="dataset-empty"><span>当前资源池没有在线 GPU 节点</span></div>
        </section>

        <aside class="preflight-panel">
          <div class="section-title"><h3>训练预检</h3><button type="button" @click="requestResources">重新检查</button></div>
          <ul>
            <li :class="{ pass: Boolean(form.modelId) }"><el-icon><CircleCheck /></el-icon><span>基础模型</span><strong>{{ form.modelId || "未选择" }}</strong></li>
            <li :class="{ pass: Boolean(selectedDataset) }"><el-icon><CircleCheck /></el-icon><span>训练数据</span><strong>{{ selectedDataset?.name || "未选择" }}</strong></li>
            <li class="pass"><el-icon><CircleCheck /></el-icon><span>训练方案</span><strong>{{ form.method === "qlora" ? "QLoRA 4-bit" : "LoRA" }}</strong></li>
            <li :class="{ pass: Boolean(selectedNode) }"><el-icon><CircleCheck /></el-icon><span>GPU 节点</span><strong>{{ selectedNode?.name || "未选择" }}</strong></li>
            <li :class="{ pass: nodeArchitectureReady }"><el-icon><CircleCheck /></el-icon><span>架构兼容</span><strong>{{ nodeArchitectureReady ? "NVIDIA GPU 可用" : "等待选择节点" }}</strong></li>
            <li :class="{ pass: nodeMemoryReady, warning: Boolean(selectedNode) && !nodeMemoryReady }"><el-icon><CircleCheck /></el-icon><span>显存容量</span><strong>{{ nodeMemoryMessage }}</strong></li>
          </ul>
          <div class="memory-estimate"><span>预计峰值显存</span><strong>{{ estimatedMemory }} GB</strong><small>按当前微调方案保守估算；节点 API 暂无实时空闲显存，提交时会再次复核。</small></div>
          <label class="auto-optimize"><span><strong>自动优化</strong><small>显存不足时调整量化、截断长度和批量。</small></span><el-switch v-model="form.autoOptimize" /></label>
        </aside>
      </div>

      <section class="submit-recap">
        <div><span>模型</span><strong>{{ form.modelId || "待选择" }} @ {{ form.requestedRevision || "main" }}</strong></div>
        <div><span>数据</span><strong>{{ selectedDataset?.name || "待选择" }}</strong></div>
        <div><span>训练</span><strong>{{ form.epochs }} epochs · cutoff {{ form.cutoffLen }} · effective batch {{ effectiveBatchSize }}</strong></div>
        <div><span>资源</span><strong>{{ selectedNode ? `${selectedNode.name} · ${nodeGpuName(selectedNode)}` : "待选择" }}</strong></div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { CircleCheck, Cpu, Document, InfoFilled, MagicStick, Search, Upload, WarningFilled } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, ref, watch } from "vue";

import { api, type ComputeNodeRecord, type DatasetRecord, type LlmDatasetPreview, type LlmDatasetValidation, type LlmModelResolution, type ResourcePoolRecord } from "@/api/client";
import { llmModelRecommendations } from "./llmModelCatalog";
import {
  applyLlmConfigYaml,
  countLlmAdvancedChanges,
  createDefaultLlmTrainingForm,
  llmEffectiveBatchSize,
  managedLlmFields,
  resetLlmAdvancedGroup,
  stringifyLlmConfig,
  type LlmAdvancedGroup,
  type LlmConfigMode,
  type LlmFineTuningMethod,
  type LlmModelSource,
  type LlmTrainingForm,
} from "./llmTrainingForm";

const props = defineProps<{
  activeStep: number;
  datasets: DatasetRecord[];
  resourcePools: ResourcePoolRecord[];
  computeNodes: ComputeNodeRecord[];
  resourcesLoading?: boolean;
  resourceError?: string;
}>();

const emit = defineEmits<{ requestResources: []; datasetUploaded: [dataset: DatasetRecord] }>();
const form = defineModel<LlmTrainingForm>({ required: true });
const modelReferenceChecked = ref(Boolean(form.value.modelId));
const modelResolving = ref(false);
const modelResolutionError = ref("");
const modelResolution = ref<LlmModelResolution | null>(null);
const configMode = ref<LlmConfigMode>("form");
const yamlText = ref("");
const yamlError = ref("");
const datasetFileInput = ref<HTMLInputElement | null>(null);
const datasetUploading = ref(false);
const datasetUploadSummary = ref<LlmDatasetValidation | null>(null);
const datasetPreview = ref<LlmDatasetPreview | null>(null);
const datasetPreviewLoading = ref(false);
const datasetPreviewError = ref("");
const datasetDetailsVisible = ref(false);

const modelSources: Array<{ label: string; value: LlmModelSource }> = [
  { label: "Hugging Face", value: "huggingface" },
  { label: "ModelScope", value: "modelscope" },
];

const recommendedModels = computed(() => llmModelRecommendations(form.value.modelSource));
const llmDatasets = computed(() => props.datasets.filter((dataset) => dataset.task === "llm" && ["validated", "ready"].includes(dataset.status)));
const selectedDataset = computed(() => props.datasets.find((dataset) => dataset.id === form.value.datasetId));
const datasetFormat = computed(() => String(selectedDataset.value?.format || selectedDataset.value?.class_schema?.format || "自动识别"));
const dataStepReady = computed(() => modelReferenceChecked.value && Boolean(selectedDataset.value));
const effectiveBatchSize = computed(() => llmEffectiveBatchSize(form.value));
const gpuResourcePools = computed(() => props.resourcePools.filter((pool) => pool.enabled && props.computeNodes.some((node) => node.resource_pool_id === pool.id && isOnlineGpuNode(node))));
const availableNodes = computed(() => props.computeNodes.filter((node) => node.resource_pool_id === form.value.poolId && isOnlineGpuNode(node)));
const selectedNode = computed(() => props.computeNodes.find((node) => node.id === form.value.nodeId));
const estimatedMemory = computed(() => (form.value.method === "qlora" ? 6.8 : 10.6));
const nodeArchitectureReady = computed(() => Boolean(selectedNode.value && isOnlineGpuNode(selectedNode.value)));
const selectedNodeMemoryMib = computed(() => nodeGpuMemoryMib(selectedNode.value));
const nodeMemoryReady = computed(() => Boolean(selectedNode.value && (!selectedNodeMemoryMib.value || selectedNodeMemoryMib.value >= estimatedMemory.value * 1024)));
const nodeMemoryMessage = computed(() => {
  if (!selectedNode.value) return "等待选择节点";
  if (!selectedNodeMemoryMib.value) return "总显存待检测";
  return nodeMemoryReady.value ? `${Math.round(selectedNodeMemoryMib.value / 1024)} GB，可运行` : `${Math.round(selectedNodeMemoryMib.value / 1024)} GB，不足`;
});
const tokenEstimates = computed(() => (datasetPreview.value?.samples ?? []).map((sample) => sample.token_estimate).sort((a, b) => a - b));
const tokenP50 = computed(() => percentile(tokenEstimates.value, 0.5));
const tokenP95 = computed(() => percentile(tokenEstimates.value, 0.95));
const truncationPercent = computed(() => {
  if (!tokenEstimates.value.length) return "0.0";
  return ((tokenEstimates.value.filter((value) => value > form.value.cutoffLen).length / tokenEstimates.value.length) * 100).toFixed(1);
});
const tokenBarHeights = computed(() => {
  const maximum = Math.max(...tokenEstimates.value, 1);
  return tokenEstimates.value.map((value) => Math.max(8, Math.round((value / maximum) * 100)));
});
const cutoffMarkerPercent = computed(() => {
  const maximum = Math.max(datasetPreview.value?.token_analysis.maximum ?? 0, form.value.cutoffLen, 1);
  return Math.min(100, Math.round((form.value.cutoffLen / maximum) * 100));
});

watch(
  () => form.value.datasetId,
  (datasetId) => {
    datasetPreview.value = null;
    datasetPreviewError.value = "";
    if (datasetId) void loadDatasetPreview(datasetId);
  },
  { immediate: true },
);

async function uploadDatasetFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const name = file.name.replace(/\.(jsonl?)$/i, "").trim() || `llm-dataset-${Date.now()}`;
  datasetUploading.value = true;
  datasetUploadSummary.value = null;
  try {
    const result = await api.uploadLlmDataset({ name, file, format: "auto" });
    datasetUploadSummary.value = result;
    emit("datasetUploaded", result.dataset);
    form.value.datasetId = result.dataset.id;
    ElMessage.success(`数据集已校验：${result.valid_count} 条有效样本`);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "大模型数据集上传失败");
  } finally {
    datasetUploading.value = false;
    input.value = "";
  }
}

async function loadDatasetPreview(datasetId: string) {
  datasetPreviewLoading.value = true;
  datasetPreviewError.value = "";
  try {
    datasetPreview.value = await api.previewLlmDataset(datasetId, 20);
  } catch (error) {
    datasetPreviewError.value = error instanceof Error ? error.message : "数据集预览加载失败";
  } finally {
    datasetPreviewLoading.value = false;
  }
}

function percentile(values: number[], percentileValue: number) {
  if (!values.length) return 0;
  const index = Math.min(values.length - 1, Math.ceil(values.length * percentileValue) - 1);
  return values[index];
}

function selectModelSource(source: LlmModelSource) {
  form.value.modelSource = source;
  if (["main", "master"].includes(form.value.requestedRevision.trim())) {
    form.value.requestedRevision = source === "modelscope" ? "master" : "main";
  }
  invalidateModelResolution();
}

function invalidateModelResolution() {
  modelReferenceChecked.value = false;
  modelResolution.value = null;
  modelResolutionError.value = "";
}

async function checkModelReference() {
  modelReferenceChecked.value = false;
  modelResolutionError.value = "";
  modelResolution.value = null;
  if (!/^[\w.-]+\/[\w.-]+$/.test(form.value.modelId.trim())) {
    modelResolutionError.value = "模型 ID 必须使用 namespace/repository 格式";
    return;
  }
  modelResolving.value = true;
  try {
    const resolution = await api.resolveLlmModel({
      source: form.value.modelSource,
      model_id: form.value.modelId.trim(),
      revision: form.value.requestedRevision.trim() || (form.value.modelSource === "modelscope" ? "master" : "main"),
    });
    modelResolution.value = resolution;
    form.value.modelId = resolution.model_id;
    form.value.resolvedRevision = resolution.resolved_revision;
    modelReferenceChecked.value = true;
  } catch (error) {
    modelResolutionError.value = error instanceof Error ? error.message : "模型仓库解析失败";
  } finally {
    modelResolving.value = false;
  }
}

function selectMethod(method: LlmFineTuningMethod) {
  form.value.method = method;
  if (method === "qlora") form.value.gradientCheckpointing = true;
}

function advancedChangeCount(group: LlmAdvancedGroup) {
  return countLlmAdvancedChanges(form.value, group);
}

function resetAdvancedGroup(group: LlmAdvancedGroup) {
  form.value = resetLlmAdvancedGroup(form.value, group);
}

function resetRecommendedConfig() {
  const current = form.value;
  const recommended = createDefaultLlmTrainingForm(current.name);
  form.value = {
    ...recommended,
    name: current.name,
    modelSource: current.modelSource,
    modelId: current.modelId,
    requestedRevision: current.requestedRevision,
    resolvedRevision: current.resolvedRevision,
    template: current.template,
    trustRemoteCode: current.trustRemoteCode,
    datasetId: current.datasetId,
    poolId: current.poolId,
    nodeId: current.nodeId,
  };
  yamlText.value = stringifyLlmConfig(form.value);
  yamlError.value = "";
  ElMessage.success("已恢复推荐训练配置");
}

async function copyYamlConfig() {
  try {
    await navigator.clipboard.writeText(yamlText.value);
    ElMessage.success("配置已复制");
  } catch {
    ElMessage.error("复制失败，请检查浏览器剪贴板权限");
  }
}

function switchConfigMode(mode: LlmConfigMode) {
  yamlError.value = "";
  if (mode === "yaml") {
    yamlText.value = stringifyLlmConfig(form.value);
    configMode.value = mode;
    return;
  }
  try {
    form.value = applyLlmConfigYaml(form.value, yamlText.value);
    configMode.value = mode;
  } catch (error) {
    yamlError.value = error instanceof Error ? error.message : "YAML 配置无效";
  }
}

function clearNodeSelection() {
  form.value.nodeId = "";
}

function requestResources() {
  emit("requestResources");
}

function isOnlineGpuNode(node: ComputeNodeRecord) {
  const gpuCount = Array.isArray(node.capabilities.gpu_uuids) ? node.capabilities.gpu_uuids.length : Number(node.resources.gpu_count || 0);
  return node.status === "online" && gpuCount > 0;
}

function nodeGpuName(node: ComputeNodeRecord) {
  const gpuModels = Array.isArray(node.capabilities.gpu_models) ? node.capabilities.gpu_models : [];
  const gpus = Array.isArray(node.resources.gpus) ? node.resources.gpus as Array<Record<string, unknown>> : [];
  return String(gpuModels[0] || gpus[0]?.name || node.resources.gpu_name || "NVIDIA GPU");
}

function nodeGpuMemory(node: ComputeNodeRecord) {
  const value = nodeGpuMemoryMib(node);
  return value > 0 ? `${Math.round(value / 1024)} GB` : "显存待检测";
}

function nodeGpuMemoryMib(node?: ComputeNodeRecord) {
  if (!node) return 0;
  const gpus = Array.isArray(node.resources.gpus) ? node.resources.gpus as Array<Record<string, unknown>> : [];
  const bytes = Number(gpus[0]?.memory_total_bytes || 0);
  return Number(node.resources.gpu_memory_total_mib || node.resources.gpu_memory_total_mb || 0) || (bytes > 0 ? bytes / 1024 / 1024 : 0);
}

function nodeLastChecked(node: ComputeNodeRecord) {
  if (!node.last_seen_at) return "SSH 探测可用";
  return `更新于 ${new Date(node.last_seen_at).toLocaleString("zh-CN", { hour12: false })}`;
}

function validateStep(step: number) {
  if (step === 0 && !form.value.name.trim()) return { valid: false, message: "请输入产线名称" };
  if (step === 1 && !modelReferenceChecked.value) return { valid: false, message: "请先解析基础模型" };
  if (step === 1 && !form.value.datasetId) return { valid: false, message: "请选择已校验的大模型数据集" };
  if (step === 2 && configMode.value === "yaml") {
    switchConfigMode("form");
    if (yamlError.value) return { valid: false, message: yamlError.value };
  }
  if (step === 3 && !form.value.poolId) return { valid: false, message: "请选择 GPU 资源池" };
  if (step === 3 && !form.value.nodeId) return { valid: false, message: "请选择在线 GPU 节点" };
  if (step === 3 && !nodeMemoryReady.value) return { valid: false, message: "所选节点总显存不足，请改用 QLoRA 或选择更大显存节点" };
  return { valid: true, message: "" };
}

defineExpose({ validateStep, configMode });
</script>

<style scoped>
.llm-wizard { color: #101828; }
.llm-step { display: grid; gap: 24px; }
.step-heading, .section-title, .scenario-title, .preview-heading, .node-title, .auto-optimize, .risk-switch { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.step-heading h2, .section-title h3, .managed-fields h3 { margin: 0; }
.step-heading h2 { font-size: 20px; }
.step-heading p, .llm-scenario p, .managed-fields p { margin: 6px 0 0; color: #667085; }
.draft-state, .section-title > span, .preview-heading > span { color: #667085; font-size: 13px; }
.llm-field { display: grid; align-content: start; gap: 8px; min-width: 0; }
.llm-field > span { color: #344054; font-size: 14px; font-weight: 600; }
.llm-field small, .risk-switch small, .switch-field small { color: #667085; font-size: 12px; line-height: 1.45; }
.required-label::before { content: "* "; color: #f04438; }
.llm-name-field { max-width: 720px; }
.llm-scenario { display: grid; grid-template-columns: 48px minmax(0, 1fr); gap: 16px; max-width: 760px; padding: 20px; border: 1px solid #1763ff; background: #f8faff; }
.scenario-icon { display: grid; width: 44px; height: 44px; place-items: center; border-radius: 6px; background: #1763ff; color: #fff; font-size: 24px; }
.scenario-title { justify-content: flex-start; }
.scenario-title em { padding: 3px 8px; border-radius: 12px; background: #e8f1ff; color: #1763ff; font-size: 12px; font-style: normal; }
.llm-scenario small { color: #475467; }
.llm-section, .method-section { padding-top: 4px; border-top: 1px solid #e4e7ec; }
.section-title { margin-bottom: 18px; padding-top: 18px; }
.source-switch, .config-mode-switch { display: inline-flex; border: 1px solid #d0d5dd; }
.source-switch button, .config-mode-switch button { min-height: 38px; padding: 0 18px; border: 0; border-right: 1px solid #d0d5dd; background: #fff; color: #475467; cursor: pointer; }
.source-switch button:last-child, .config-mode-switch button:last-child { border-right: 0; }
.source-switch button.active, .config-mode-switch button.active { background: #eef4ff; color: #1763ff; font-weight: 600; }
.model-entry-grid { display: grid; grid-template-columns: minmax(320px, 1.5fr) minmax(220px, 0.8fr) auto; align-items: end; gap: 16px; margin-top: 18px; }
.model-id-select { width: 100%; }
.model-option { display: grid; grid-template-columns: minmax(110px, auto) minmax(180px, 1fr) auto; align-items: center; gap: 12px; width: 100%; }
.model-option strong { color: #101828; }
.model-option small { overflow: hidden; color: #667085; text-overflow: ellipsis; white-space: nowrap; }
.model-option em { color: #475467; font-size: 12px; font-style: normal; }
.resolve-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 40px; padding: 0 18px; border: 1px solid #1763ff; background: #1763ff; color: #fff; cursor: pointer; }
.resolve-button:disabled { border-color: #d0d5dd; background: #eaecf0; color: #98a2b3; cursor: not-allowed; }
.model-resolution, .compatibility-result, .recommendation, .yaml-status { display: flex; align-items: center; gap: 10px; margin-top: 16px; padding: 12px 14px; border: 1px solid #b2ddff; background: #f5fbff; color: #175cd3; }
.model-resolution div { display: grid; gap: 3px; }
.model-resolution small { margin-left: auto; color: #667085; }
.model-resolution.error { border-color: #fecdca; background: #fff6f5; color: #b42318; }
.inline-advanced { margin-top: 16px; border-top: 1px solid #eaecf0; padding-top: 14px; }
.inline-advanced summary, .advanced-groups summary { cursor: pointer; color: #344054; font-weight: 600; }
.advanced-inline-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 0.7fr); gap: 24px; margin-top: 16px; }
.risk-switch, .switch-field { min-height: 70px; padding: 12px 14px; border: 1px solid #e4e7ec; }
.risk-switch > span, .switch-field > span { display: grid; gap: 4px; }
.packing-field, .gradient-checkpoint-field { display: grid; min-width: 0; align-content: start; gap: 8px; }
.packing-field--full-row, .gradient-checkpoint-field--full-row { grid-column: 1 / -1; }
.packing-field__header, .gradient-checkpoint-field__header { display: flex; min-height: 32px; align-items: center; justify-content: space-between; gap: 16px; }
.packing-field strong, .gradient-checkpoint-field strong { color: #344054; font-size: 14px; font-weight: 600; }
.packing-field small, .gradient-checkpoint-field small { color: #667085; font-size: 12px; line-height: 1.45; }
.dataset-field { max-width: 720px; }
.section-action { margin-left: auto; padding: 0; border: 0; background: transparent; color: #1763ff; font: inherit; cursor: pointer; }
.dataset-actions { display: flex; align-items: center; gap: 12px; margin-top: 12px; color: #667085; font-size: 12px; }
.dataset-upload-button { display: inline-flex; min-height: 34px; align-items: center; gap: 7px; padding: 0 14px; border: 1px solid #b9cdfd; background: #fff; color: #1763ff; cursor: pointer; }
.dataset-upload-button:hover { border-color: #1763ff; background: #f5f8ff; }
.dataset-upload-button:disabled { cursor: wait; opacity: 0.6; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
.upload-summary { display: flex; gap: 12px; margin-top: 10px; color: #027a48; font-size: 13px; }
.full-width { width: 100%; }
.dataset-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 18px; border: 1px solid #e4e7ec; }
.dataset-summary div { display: grid; gap: 6px; padding: 14px 16px; border-right: 1px solid #e4e7ec; }
.dataset-summary div:last-child { border-right: 0; }
.dataset-summary span { color: #667085; font-size: 12px; }
.success-text { color: #027a48; }
.token-analysis { margin-top: 16px; padding: 16px; border: 1px solid #e4e7ec; background: #fff; }
.token-analysis .preview-heading { padding: 0 0 12px; border: 0; background: transparent; }
.token-bars { position: relative; display: flex; height: 132px; align-items: end; gap: 5px; overflow: hidden; padding: 18px 10px 0; border-bottom: 1px solid #98a2b3; background: #f9fafb; }
.token-bars > i { flex: 1 1 0; min-width: 4px; max-width: 22px; border-radius: 2px 2px 0 0; background: #5b8def; }
.cutoff-marker { position: absolute; top: 0; bottom: 0; width: 1px; border-left: 1px dashed #f04438; }
.cutoff-marker span { position: absolute; top: 2px; right: 5px; color: #b42318; font-size: 11px; font-weight: 500; white-space: nowrap; }
.token-analysis > p { margin: 10px 0 0; color: #667085; font-size: 13px; }
.conversation-preview { margin-top: 16px; border: 1px solid #e4e7ec; }
.preview-heading { padding: 12px 16px; border-bottom: 1px solid #e4e7ec; background: #f9fafb; }
.message-row { display: grid; grid-template-columns: 92px minmax(0, 1fr); padding: 12px 16px; border-bottom: 1px solid #f2f4f7; }
.message-row:last-child { border-bottom: 0; }
.message-row em { color: #475467; font-style: normal; font-weight: 600; }
.message-row p { margin: 0; color: #344054; white-space: pre-wrap; }
.message-row.assistant { background: #f8faff; }
.preview-state { margin: 0; padding: 18px; color: #667085; text-align: center; }
.preview-state.error { color: #b42318; }
.preview-state button { margin-left: 8px; padding: 0; border: 0; background: transparent; color: #1763ff; cursor: pointer; }
.dataset-detail-backdrop { position: fixed; z-index: 3000; inset: 0; display: flex; justify-content: flex-end; background: rgb(16 24 40 / 38%); }
.dataset-detail-drawer { width: min(560px, 92vw); height: 100%; overflow: auto; padding: 0 24px 32px; background: #fff; box-shadow: -10px 0 30px rgb(16 24 40 / 12%); }
.dataset-detail-drawer > header { position: sticky; z-index: 1; top: 0; display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 22px 0 16px; border-bottom: 1px solid #e4e7ec; background: #fff; }
.dataset-detail-drawer h3, .dataset-detail-drawer h4 { margin: 0; }
.dataset-detail-drawer header p { margin: 5px 0 0; color: #667085; }
.dataset-detail-drawer header button { width: 32px; height: 32px; border: 0; background: transparent; color: #475467; font-size: 24px; cursor: pointer; }
.dataset-detail-drawer > section { padding: 20px 0; border-bottom: 1px solid #eaecf0; }
.dataset-detail-drawer dl { display: grid; grid-template-columns: minmax(110px, .4fr) minmax(0, 1fr); margin: 14px 0 0; border: 1px solid #e4e7ec; }
.dataset-detail-drawer dt, .dataset-detail-drawer dd { margin: 0; padding: 9px 12px; border-bottom: 1px solid #e4e7ec; overflow-wrap: anywhere; }
.dataset-detail-drawer dt { background: #f9fafb; color: #475467; }
.drawer-sample { display: grid; gap: 6px; margin-top: 12px; padding: 12px; background: #f9fafb; }
.drawer-sample span { color: #475467; line-height: 1.5; }
.dataset-empty { display: flex; min-height: 112px; align-items: center; justify-content: center; gap: 10px; margin-top: 16px; border: 1px dashed #d0d5dd; color: #667085; }
.compatibility-result { margin-top: 0; border-color: #e4e7ec; background: #f9fafb; color: #667085; }
.compatibility-result.ready { border-color: #abefc6; background: #ecfdf3; color: #027a48; }
.parameter-heading { align-items: flex-start; }
.method-options { display: grid; grid-template-columns: repeat(2, minmax(0, 300px)); gap: 12px; }
.method-options button { position: relative; display: grid; gap: 6px; min-height: 78px; padding: 14px 16px; border: 1px solid #d0d5dd; background: #fff; text-align: left; cursor: pointer; }
.method-options button.selected { border-color: #1763ff; background: #f8faff; box-shadow: 0 0 0 1px #1763ff inset; }
.method-options span { color: #667085; font-size: 13px; }
.method-options em { position: absolute; top: 10px; right: 10px; padding: 2px 7px; border-radius: 10px; background: #e8f1ff; color: #1763ff; font-size: 12px; font-style: normal; }
.recommendation { max-width: 720px; }
.parameter-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px 20px; }
.stability-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.parameter-grid :deep(.el-input-number), .parameter-grid :deep(.el-select) { width: 100%; }
.advanced-groups { display: grid; border-top: 1px solid #e4e7ec; }
.advanced-groups details { padding: 16px 0; border-bottom: 1px solid #e4e7ec; }
.advanced-groups summary { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.advanced-groups summary > span:first-child { display: flex; align-items: baseline; gap: 14px; }
.advanced-groups summary small { color: #667085; font-weight: 400; }
.advanced-meta { display: inline-flex; align-items: center; gap: 14px; }
.advanced-meta em { color: #667085; font-size: 12px; font-style: normal; font-weight: 400; }
.advanced-meta button, .yaml-toolbar button { padding: 0; border: 0; background: transparent; color: #1763ff; cursor: pointer; }
.compact-grid { margin-top: 18px; }
.yaml-layout { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 20px; }
.yaml-toolbar { display: flex; min-height: 42px; align-items: center; justify-content: space-between; gap: 16px; padding: 0 12px; border: 1px solid #98b9ff; border-bottom: 0; background: #f5f8ff; color: #344054; font-size: 13px; }
.yaml-toolbar > div { display: flex; gap: 16px; }
.yaml-editor { width: 100%; min-height: 540px; resize: vertical; padding: 16px; border: 1px solid #98b9ff; outline: none; background: #fbfcff; color: #172b4d; font: 13px/1.6 Consolas, monospace; }
.yaml-status.error { border-color: #fecdca; background: #fef3f2; color: #b42318; }
.managed-fields { align-self: start; display: grid; gap: 8px; padding: 16px; border: 1px solid #e4e7ec; background: #f9fafb; }
.managed-fields code { padding: 5px 7px; background: #fff; color: #475467; font-size: 12px; }
.resource-preflight-layout { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 24px; }
.resource-section { border-top: 0; padding-top: 0; }
.node-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.node-options button { display: grid; gap: 7px; min-height: 100px; padding: 14px; border: 1px solid #d0d5dd; background: #fff; color: #344054; text-align: left; cursor: pointer; }
.node-options button.selected { border-color: #1763ff; background: #f8faff; box-shadow: 0 0 0 1px #1763ff inset; }
.node-title { justify-content: flex-start; }
.node-title i { width: 8px; height: 8px; border-radius: 50%; background: #12b76a; box-shadow: 0 0 0 3px #d1fadf; }
.node-title em { margin-left: auto; color: #027a48; font-size: 12px; font-style: normal; }
.node-options small { color: #667085; }
.resource-message { color: #667085; }
.resource-message.error { color: #b42318; }
.preflight-panel { align-self: start; padding: 18px; border: 1px solid #d0d5dd; background: #f9fafb; }
.preflight-panel .section-title { margin: 0 0 14px; padding: 0; }
.preflight-panel .section-title button { border: 0; background: transparent; color: #1763ff; cursor: pointer; }
.preflight-panel ul { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
.preflight-panel li { display: grid; grid-template-columns: 20px 88px minmax(0, 1fr); gap: 8px; padding: 10px 0; border-bottom: 1px solid #e4e7ec; color: #98a2b3; }
.preflight-panel li.pass { color: #027a48; }
.preflight-panel li.warning { color: #b54708; }
.preflight-panel li strong { overflow: hidden; color: #344054; text-overflow: ellipsis; white-space: nowrap; }
.memory-estimate { display: grid; grid-template-columns: 1fr auto; gap: 5px 12px; margin-top: 16px; padding: 12px; background: #fff; }
.memory-estimate small { grid-column: 1 / -1; color: #667085; }
.auto-optimize { margin-top: 14px; }
.auto-optimize > span { display: grid; gap: 4px; }
.auto-optimize small { color: #667085; font-size: 12px; }
.submit-recap { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid #e4e7ec; }
.submit-recap div { display: grid; gap: 6px; padding: 14px 16px; border-right: 1px solid #e4e7ec; }
.submit-recap div:last-child { border-right: 0; }
.submit-recap span { color: #667085; font-size: 12px; }
.submit-recap strong { overflow-wrap: anywhere; }

@media (max-width: 1180px) {
  .parameter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .resource-preflight-layout, .yaml-layout { grid-template-columns: 1fr; }
  .submit-recap { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .submit-recap div:nth-child(2) { border-right: 0; }
  .submit-recap div:nth-child(-n + 2) { border-bottom: 1px solid #e4e7ec; }
}

@media (max-width: 820px) {
  .step-heading, .parameter-heading, .section-title { align-items: flex-start; flex-direction: column; }
  .model-entry-grid, .advanced-inline-grid, .parameter-grid, .node-options, .dataset-summary, .submit-recap { grid-template-columns: 1fr; }
  .dataset-summary div, .submit-recap div { border-right: 0; border-bottom: 1px solid #e4e7ec; }
  .dataset-summary div:last-child, .submit-recap div:last-child { border-bottom: 0; }
  .method-options { grid-template-columns: 1fr; }
  .model-resolution { align-items: flex-start; flex-direction: column; }
  .model-resolution small { margin-left: 0; }
  .dataset-detail-drawer { width: 100%; max-width: none; }
  .advanced-groups summary, .advanced-groups summary > span:first-child { align-items: flex-start; flex-direction: column; }
  .yaml-toolbar { align-items: flex-start; flex-direction: column; padding-block: 10px; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }
}
</style>
