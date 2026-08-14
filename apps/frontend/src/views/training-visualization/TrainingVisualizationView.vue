<template>
  <section class="training-visualization-view">
    <header class="visualization-page-header">
      <div>
        <h1>可视化训练</h1>
        <p>集中查看训练进度、指标趋势和资源使用情况</p>
      </div>
      <div class="header-actions">
        <button class="refresh-button" type="button" :disabled="listLoading" @click="loadRuns">
          <el-icon><Refresh /></el-icon>
          刷新
        </button>
        <div class="advanced-menu-wrap">
          <button
            class="icon-button"
            type="button"
            data-testid="advanced-menu-toggle"
            title="高级工具"
            aria-label="高级工具"
            :aria-expanded="advancedMenuOpen"
            @click="advancedMenuOpen = !advancedMenuOpen"
          >
            <el-icon><MoreFilled /></el-icon>
          </button>
          <div v-if="advancedMenuOpen" class="advanced-menu">
            <button
              v-for="action in secondaryActions"
              :key="action.source"
              type="button"
              :data-testid="`open-${action.source}`"
              @click="openExternalTool(action.url)"
            >
              {{ externalActionLabel(action.source) }}
            </button>
            <span v-if="secondaryActions.length === 0">未配置外部工具</span>
          </div>
        </div>
      </div>
    </header>

    <div class="visualization-layout">
      <aside class="run-list-panel">
        <div class="run-list-heading">
          <strong>训练记录</strong>
          <span>{{ jobs.length }} 条</span>
        </div>
        <div v-if="listLoading" class="run-list-state">正在加载训练记录...</div>
        <el-empty v-else-if="jobs.length === 0" description="暂无训练记录" />
        <div
          v-for="job in jobs"
          v-else
          :key="job.id"
          class="run-row"
          :class="{ active: selectedJob?.id === job.id }"
        >
          <button
            class="run-item"
            :class="{ active: selectedJob?.id === job.id }"
            :data-testid="`job-${job.id}`"
            type="button"
            @click="selectJob(job)"
          >
            <span class="run-title">{{ pipelineName(job.pipeline_id) }}</span>
            <span class="run-meta">
              <el-tag :type="statusType(job.status)" size="small" effect="light">{{ statusLabel(job.status) }}</el-tag>
              <time>{{ formatDate(job.created_at) }}</time>
            </span>
            <code>{{ observationRunName(job) }}</code>
          </button>
          <button
            class="delete-run-button"
            type="button"
            :disabled="!canDeleteRun(job)"
            :title="canDeleteRun(job) ? '删除训练记录' : '请先停止训练'"
            :aria-label="`删除训练记录 ${pipelineName(job.pipeline_id)}`"
            :data-testid="`delete-job-${job.id}`"
            @click="deleteRun(job)"
          >
            <el-icon><Delete /></el-icon>
          </button>
        </div>
      </aside>

      <main class="visualization-workspace">
        <template v-if="selectedJob">
          <div class="selected-run-band">
            <div>
              <span class="band-label">当前训练</span>
              <strong>{{ pipelineName(selectedJob.pipeline_id) }}</strong>
            </div>
            <code>{{ observationRunName(selectedJob) }}</code>
            <el-tag :type="statusType(currentStatus)" size="small" effect="light">
              {{ statusLabel(currentStatus) }}
            </el-tag>
          </div>

          <nav class="dashboard-tabs" role="tablist" aria-label="训练可视化视图">
            <button
              v-for="tab in tabs"
              :key="tab.id"
              type="button"
              role="tab"
              :aria-selected="activeTab === tab.id"
              :class="{ active: activeTab === tab.id }"
              :data-testid="`tab-${tab.id}`"
              @click="activateTab(tab.id)"
            >
              {{ tab.label }}
            </button>
          </nav>

          <div class="dashboard-panels">
            <section v-show="activeTab === 'overview'" class="dashboard-panel" data-testid="overview-panel">
              <div v-if="summaryLoading && !summaryData" class="panel-loading">正在加载训练概览...</div>
              <template v-else>
                <div class="overview-band provenance-band" data-testid="run-provenance">
                  <div class="provenance-heading"><strong>运行溯源</strong><span>不可变训练快照</span></div>
                  <div class="provenance-grid">
                    <div class="provenance-cell"><span>框架</span><strong>{{ frameworkLabel }}</strong></div>
                    <div class="provenance-cell"><span>模型</span><strong>{{ modelLabel }}</strong></div>
                    <div class="provenance-cell"><span>数据集版本</span><strong>{{ datasetVersionLabel }}</strong></div>
                    <div class="provenance-cell"><span>运行镜像</span><code>{{ imageDigestLabel }}</code></div>
                    <div class="provenance-cell"><span>Adapter</span><strong>{{ adapterVersionLabel }}</strong></div>
                    <label v-if="attempts.length" class="provenance-cell attempt-cell"><span>训练尝试</span><select v-model="selectedAttemptId" data-testid="attempt-select" @change="selectAttempt"><option v-for="attempt in attempts" :key="attempt.id" :value="attempt.id">Attempt {{ attempt.attempt_number }}</option></select></label>
                  </div>
                </div>
                <LlmTrainingOverview
                  v-if="isLlmRun && summaryData"
                  :summary="summaryData"
                  :mlflow-url="mlflowUrl"
                  :tensorboard-url="tensorboardUrl"
                />
                <template v-else>
                <div class="overview-band progress-band">
                  <div class="status-cell">
                    <span>训练状态</span>
                    <strong>{{ statusLabel(currentStatus) }}</strong>
                  </div>
                  <div class="progress-cell">
                    <div class="progress-heading">
                      <span>训练进度</span>
                      <strong>{{ formatPercentNumber(progressPercent) }}</strong>
                    </div>
                    <el-progress :percentage="progressPercent" :stroke-width="8" :show-text="false" />
                  </div>
                </div>

                <div class="overview-band metric-grid detail-grid">
                  <div class="metric-cell"><span>当前 Epoch</span><strong>{{ currentEpoch }} / {{ totalEpochs }}</strong></div>
                  <div class="metric-cell"><span>已用时间</span><strong>{{ formatDuration(elapsedSeconds) }}</strong></div>
                  <div class="metric-cell"><span>预计剩余</span><strong>{{ formatDuration(etaSeconds) }}</strong></div>
                  <div class="metric-cell"><span>设备</span><strong>{{ deviceLabel }}</strong></div>
                  <div class="metric-cell"><span>Batch</span><strong>{{ batchLabel }}</strong></div>
                  <div class="metric-cell"><span>图像尺寸</span><strong>{{ imageSizeLabel }}</strong></div>
                  <div class="metric-cell"><span>学习率</span><strong>{{ learningRateLabel }}</strong></div>
                </div>

                <div class="overview-band latest-band">
                  <div class="band-title">
                    <strong>最新训练指标</strong>
                    <span>最近一次记录</span>
                  </div>
                  <div class="metric-grid latest-grid">
                    <div class="metric-cell"><span>Precision</span><strong>{{ latestMetricLabel("precision") }}</strong></div>
                    <div class="metric-cell"><span>Recall</span><strong>{{ latestMetricLabel("recall") }}</strong></div>
                    <div class="metric-cell"><span>mAP50</span><strong>{{ latestMetricLabel("map50") }}</strong></div>
                    <div class="metric-cell"><span>mAP50-95</span><strong>{{ latestMetricLabel("map50_95") }}</strong></div>
                  </div>
                </div>

                <div class="overview-band source-band">
                  <div class="band-title">
                    <strong>数据源</strong>
                    <span>原生接口可用性</span>
                  </div>
                  <div class="source-list">
                    <div v-for="source in sourceEntries" :key="source.name" class="source-item">
                      <span class="source-dot" :class="{ available: source.available }" />
                      <strong>{{ source.label }}</strong>
                      <span>{{ source.available ? "可用" : "不可用" }}</span>
                      <small v-if="!source.available && source.reason">{{ source.reason }}</small>
                    </div>
                  </div>
                </div>
                </template>
              </template>
            </section>

            <section
              v-if="metricsActivated"
              v-show="activeTab === 'metrics'"
              class="dashboard-panel chart-panel"
              data-testid="metrics-panel"
            >
              <LlmTrainingMetrics v-if="isLlmRun" :response="llmMetricResponse" />
              <template v-else>
              <div class="panel-heading">
                <div><strong>训练指标</strong><span>Epoch 标量趋势</span></div>
                <span v-if="metricsUnavailableText" class="source-warning">{{ metricsUnavailableText }}</span>
              </div>
              <div class="metrics-toolbar">
                <div class="metrics-mode-switch" role="tablist" aria-label="指标查看方式">
                  <button
                    type="button"
                    role="tab"
                    data-testid="metrics-mode-single"
                    :class="{ active: metricsMode === 'single' }"
                    :aria-selected="metricsMode === 'single'"
                    @click="metricsMode = 'single'"
                  >单次训练</button>
                  <button
                    type="button"
                    role="tab"
                    data-testid="metrics-mode-compare"
                    :class="{ active: metricsMode === 'compare' }"
                    :aria-selected="metricsMode === 'compare'"
                    @click="metricsMode = 'compare'"
                  >运行对比</button>
                </div>
                <label v-if="metricsMode === 'single'" class="smoothing-control">
                  <span>平滑</span>
                  <input v-model.number="metricSmoothing" type="range" min="0" max="0.9" step="0.1" />
                  <output>{{ metricSmoothing.toFixed(1) }}</output>
                </label>
              </div>

              <TrainingRunComparison
                v-if="metricsMode === 'compare'"
                :key="selectedJob.id"
                :jobs="jobs"
                :pipeline-names="pipelineNameMap"
                :initial-job-id="selectedJob.id"
              />
              <template v-else>
                <div v-if="metricsLoading && !metricsChartMounted" class="panel-loading">正在加载指标...</div>
                <el-empty v-else-if="!metricsChartMounted" description="该训练暂无可用指标" />
                <div v-else class="metric-card-grid">
                  <TrainingMetricCard
                    v-for="card in metricCards"
                    :key="card.id"
                    :card="card"
                    :smoothing="metricSmoothing"
                  />
                </div>
              </template>
              </template>
            </section>

            <section
              v-if="resourcesActivated"
              v-show="activeTab === 'resources'"
              class="dashboard-panel chart-panel"
              data-testid="resources-panel"
            >
              <LlmTrainingResources v-if="isLlmRun" :response="llmResourceResponse" />
              <template v-else>
              <div class="panel-heading">
                <div><strong>资源监控</strong><span>CPU、内存与显存趋势</span></div>
              </div>
              <div v-if="resourcesLoading && !resourcesChartMounted" class="panel-loading">正在加载资源数据...</div>
              <el-empty v-else-if="!resourcesChartMounted" description="该训练暂无资源记录" />
              <MetricLineChart v-if="resourcesChartMounted" :series="resourceSeries" height="420px" />
              </template>
            </section>

            <section
              v-if="analysisActivated"
              v-show="activeTab === 'analysis'"
              class="dashboard-panel"
              data-testid="analysis-panel"
            >
              <LlmTrainingAnalysis
                v-if="isLlmRun"
                :analysis="llmAnalysis"
                :artifacts="llmArtifacts"
              />
              <PaddleXTrainingAnalysis
                v-else-if="isPaddlexRun"
                :series="scalarSeries"
                :analysis="llmAnalysis"
                :artifacts="llmArtifacts"
              />
              <KeepAlive :max="5">
                <ArtifactGallery
                  v-if="!isLlmRun && !isPaddlexRun && activeTab === 'analysis'"
                  :key="selectedJob.id"
                  :job-id="selectedJob.id"
                />
              </KeepAlive>
            </section>
            <section
              v-if="artifactsActivated"
              v-show="activeTab === 'artifacts'"
              class="dashboard-panel artifact-dashboard-panel"
              data-testid="artifacts-panel"
            >
              <div v-if="artifactsLoading" class="panel-loading">正在加载训练产物...</div>
              <el-empty v-else-if="llmArtifacts.items.length === 0" description="该训练暂无可下载产物" />
              <div v-else class="observability-artifact-list">
                <button v-for="artifact in llmArtifacts.items" :key="artifact.path" type="button" :data-testid="`observability-artifact-${artifact.path}`" @click="openObservabilityArtifact(artifact)">
                  <span><strong>{{ artifact.path }}</strong><small>{{ artifact.sha256 }}</small></span>
                  <span>{{ formatArtifactSize(artifact.size_bytes) }}</span>
                </button>
              </div>
            </section>
            <section
              v-if="activeTab === 'logs'"
              class="dashboard-panel log-dashboard-panel"
              data-testid="logs-panel"
            >
              <LogStreamViewer
                v-if="selectedJob.log_stream_id"
                :key="selectedJob.log_stream_id"
                :stream-id="selectedJob.log_stream_id"
              />
              <el-empty v-else description="该训练暂无实时日志流" />
            </section>
          </div>
        </template>
        <el-empty v-else description="请选择一条训练记录" />
      </main>
    </div>
  </section>
</template>

<script setup lang="ts">
import { Delete, MoreFilled, Refresh } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import {
  api,
  type TrainingJobRecord,
  type TrainingObservabilityAnalysis,
  type TrainingObservabilityArtifacts,
  type TrainingObservabilityAvailability,
  type TrainingObservabilityResources,
  type TrainingObservabilityScalars,
  type TrainingObservabilitySummary,
  type TrainingPipelineRecord,
} from "@/api/client";
import ArtifactGallery from "@/components/training/ArtifactGallery.vue";
import LogStreamViewer from "@/components/logs/LogStreamViewer.vue";
import MetricLineChart from "@/components/training/MetricLineChart.vue";
import TrainingMetricCard from "@/components/training/TrainingMetricCard.vue";
import TrainingRunComparison from "@/components/training/TrainingRunComparison.vue";
import { buildMetricCards } from "@/components/training/trainingMetricCatalog";
import LlmTrainingAnalysis from "@/features/training-observability/llm/LlmTrainingAnalysis.vue";
import LlmTrainingMetrics from "@/features/training-observability/llm/LlmTrainingMetrics.vue";
import LlmTrainingOverview from "@/features/training-observability/llm/LlmTrainingOverview.vue";
import LlmTrainingResources from "@/features/training-observability/llm/LlmTrainingResources.vue";
import type { LlmArtifact } from "@/features/training-observability/llm/llmMetricCatalog";
import PaddleXTrainingAnalysis from "@/features/training-observability/paddlex/PaddleXTrainingAnalysis.vue";

type DashboardTab = "overview" | "metrics" | "resources" | "analysis" | "logs" | "artifacts";
type MetricsMode = "single" | "compare";

const POLL_INTERVAL_MS = 5000;
const ACTIVE_STATUSES = new Set(["queued", "running"]);
const route = useRoute();
const tabs: Array<{ id: DashboardTab; label: string }> = [
  { id: "overview", label: "概览" },
  { id: "metrics", label: "指标" },
  { id: "resources", label: "资源" },
  { id: "analysis", label: "分析" },
  { id: "logs", label: "日志" },
  { id: "artifacts", label: "产物" },
];

const jobs = ref<TrainingJobRecord[]>([]);
const pipelines = ref<TrainingPipelineRecord[]>([]);
const selectedJob = ref<TrainingJobRecord | null>(null);
const summaryData = ref<TrainingObservabilitySummary | null>(null);
const scalarSeries = ref<TrainingObservabilityScalars["series"]>({});
const resourceSeries = ref<TrainingObservabilityResources["series"]>({});
const scalarAvailability = ref<TrainingObservabilityAvailability>({});
const resourceAvailability = ref<TrainingObservabilityAvailability>({});
const llmAnalysis = ref<TrainingObservabilityAnalysis>({ findings: [], availability: {} });
const llmArtifacts = ref<TrainingObservabilityArtifacts>({ items: [], availability: {} });
const activeTab = ref<DashboardTab>("overview");
const metricsMode = ref<MetricsMode>("single");
const metricSmoothing = ref(0);
const listLoading = ref(false);
const summaryLoading = ref(false);
const metricsLoading = ref(false);
const resourcesLoading = ref(false);
const metricsActivated = ref(false);
const resourcesActivated = ref(false);
const analysisActivated = ref(false);
const artifactsActivated = ref(false);
const metricsChartMounted = ref(false);
const resourcesChartMounted = ref(false);
const artifactsLoading = ref(false);
const advancedMenuOpen = ref(false);
const selectedAttemptId = ref("");
let metricsLoadedJobId: string | null = null;
let resourcesLoadedJobId: string | null = null;
let analysisLoadedJobId: string | null = null;
let artifactsLoadedJobId: string | null = null;
let generation = 0;
let pollTimer: ReturnType<typeof setTimeout> | null = null;

const currentStatus = computed(() => summaryData.value?.status || selectedJob.value?.status || "queued");
const secondaryActions = computed(() => summaryData.value?.secondary_actions ?? []);
const mlflowUrl = computed(() => secondaryActions.value.find((action) => action.source === "mlflow")?.url);
const tensorboardUrl = computed(() => secondaryActions.value.find((action) => action.source === "tensorboard")?.url);
const selectedPipeline = computed(() => pipelines.value.find((pipeline) => pipeline.id === selectedJob.value?.pipeline_id));
const isLlmRun = computed(() => summaryData.value?.engine === "llamafactory" || selectedPipeline.value?.engine === "llamafactory");
const isPaddlexRun = computed(() => summaryData.value?.engine === "paddlex" || selectedPipeline.value?.engine === "paddlex");
const llmMetricResponse = computed(() => ({ series: scalarSeries.value, availability: scalarAvailability.value }));
const llmResourceResponse = computed(() => ({ series: resourceSeries.value, availability: resourceAvailability.value }));
const resolvedSnapshot = computed(() => selectedJob.value?.resolved_snapshot);
const attempts = computed(() => selectedJob.value?.attempts ?? []);
const frameworkLabel = computed(() => ({ yolo26: "Ultralytics", ultralytics: "Ultralytics", paddlex: "PaddleX", llamafactory: "LLaMA-Factory" } as Record<string, string>)[summaryData.value?.engine ?? selectedPipeline.value?.engine ?? ""] ?? "-");
const modelLabel = computed(() => resolvedSnapshot.value?.model?.runtime_id ?? resolvedSnapshot.value?.model?.family ?? selectedPipeline.value?.model_family ?? "-");
const datasetVersionLabel = computed(() => {
  const version = resolvedSnapshot.value?.dataset?.version ?? readNumber(selectedJob.value?.metrics?.dataset_snapshot as Record<string, unknown> | undefined, ["dataset_version"]);
  return version === null || version === undefined ? "-" : `v${version}`;
});
const imageDigestLabel = computed(() => formatImageDigest(resolvedSnapshot.value?.runtime_image_digest));
const adapterVersionLabel = computed(() => resolvedSnapshot.value?.adapter_version ?? selectedPipeline.value?.adapter_version ?? "-");
const progressPercent = computed(() => clamp(readNumber(summaryData.value?.progress, ["percent"]) ?? 0, 0, 100));
const currentEpoch = computed(() => readNumber(summaryData.value?.progress, ["current_epoch"]) ?? 0);
const totalEpochs = computed(() =>
  readNumber(summaryData.value?.progress, ["total_epochs"])
  ?? readNumber(selectedJob.value?.params, ["epochs"])
  ?? 0,
);
const elapsedSeconds = computed(() => readNumber(summaryData.value?.timing, ["elapsed_seconds"]));
const etaSeconds = computed(() => readNumber(summaryData.value?.timing, ["eta_seconds"]));
const deviceLabel = computed(() => formatDevice(
  readValue(summaryData.value?.environment, ["device"])
  ?? readValue(selectedJob.value?.environment, ["device"])
  ?? readValue(selectedJob.value?.params, ["device"]),
));
const batchLabel = computed(() => formatPlainValue(
  readValue(summaryData.value?.environment, ["batch"])
  ?? readValue(selectedJob.value?.params, ["batch"]),
));
const imageSizeLabel = computed(() => formatImageSize(
  readValue(summaryData.value?.environment, ["imgsz", "image_size"])
  ?? readValue(selectedJob.value?.params, ["imgsz", "image_size"]),
));
const learningRateLabel = computed(() => formatPlainValue(
  latestMetric("learning_rate")
  ?? readValue(selectedJob.value?.params, ["lr0", "learning_rate"]),
));
const hasMetricSeries = computed(() => hasPoints(scalarSeries.value));
const hasResourceSeries = computed(() => hasPoints(resourceSeries.value));
const metricCards = computed(() => buildMetricCards(scalarSeries.value));
const pipelineNameMap = computed(() => Object.fromEntries(pipelines.value.map((pipeline) => [pipeline.id, pipeline.name])));
const sourceEntries = computed(() => Object.entries(summaryData.value?.availability ?? {}).map(([name, value]) => ({
  name,
  label: sourceLabel(name),
  available: value.available,
  reason: value.reason,
})));
const metricsUnavailableText = computed(() => Object.entries(scalarAvailability.value)
  .filter(([, value]) => !value.available)
  .map(([name]) => `${sourceLabel(name)} 不可用`)
  .join(" · "));

function pipelineName(pipelineId: string) {
  return pipelines.value.find((pipeline) => pipeline.id === pipelineId)?.name || pipelineId;
}

function observationRunName(job: TrainingJobRecord) {
  const observability = job.metrics?.observability;
  if (observability && typeof observability === "object") {
    const values = observability as Record<string, unknown>;
    const runName = values.tensorboard_run_name || values.mlflow_run_name;
    if (typeof runName === "string" && runName) return runName;
  }
  return `job-${job.id}`;
}

function statusLabel(status: string) {
  return ({
    queued: "等待训练",
    running: "训练中",
    succeeded: "训练成功",
    success: "训练成功",
    failed: "训练失败",
    canceled: "训练中止",
  } as Record<string, string>)[status.toLowerCase()] || status;
}

function statusType(status: string): "success" | "warning" | "danger" | "info" {
  const normalized = status.toLowerCase();
  if (normalized === "succeeded" || normalized === "success") return "success";
  if (normalized === "running") return "warning";
  if (normalized === "failed" || normalized === "canceled") return "danger";
  return "info";
}

function formatDate(value?: string) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "-";
}

function readValue(source: Record<string, unknown> | undefined, keys: string[]) {
  if (!source) return null;
  for (const key of keys) {
    const value = source[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return null;
}

function readNumber(source: Record<string, unknown> | undefined, keys: string[]) {
  const value = readValue(source, keys);
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function normalizeMetricKey(name: string) {
  return name.toLowerCase()
    .replace(/\//g, ".")
    .replace(/\([^)]*\)/g, "")
    .replace(/-/g, "_")
    .replace(/[^a-z0-9_.]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^[._]+|[._]+$/g, "");
}

function latestMetric(name: string) {
  const metrics = summaryData.value?.latest_metrics ?? {};
  for (const [key, value] of Object.entries(metrics)) {
    const normalized = normalizeMetricKey(key);
    if (normalized === name || normalized.endsWith(`.${name}`)) return value;
  }
  return null;
}

function latestMetricLabel(name: string) {
  const value = latestMetric(name);
  if (value === null) return "-";
  const percent = Math.abs(value) <= 1 ? value * 100 : value;
  return `${percent.toFixed(2)}%`;
}

function formatPercentNumber(value: number) {
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}%`;
}

function formatDuration(value: number | null) {
  if (value === null || value < 0) return "-";
  const totalSeconds = Math.round(value);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}小时${minutes}分`;
  if (minutes > 0) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}

function formatDevice(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  const text = String(value);
  if (text.toLowerCase() === "cpu") return "CPU";
  if (/^\d+(,\d+)*$/.test(text)) return `GPU ${text}`;
  return text;
}

function formatPlainValue(value: unknown) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

function formatImageSize(value: unknown) {
  if (typeof value === "number" || (typeof value === "string" && /^\d+$/.test(value))) {
    return `${value} x ${value}`;
  }
  if (Array.isArray(value) && value.length >= 2) return `${value[0]} x ${value[1]}`;
  return formatPlainValue(value);
}

function observationKey(job: TrainingJobRecord) {
  return `${job.id}:${selectedAttemptId.value || "current"}`;
}

function selectedAttemptParams() {
  return selectedAttemptId.value ? { attempt_id: selectedAttemptId.value } : undefined;
}

function loadAttemptSummary(jobId: string) {
  const params = selectedAttemptParams();
  return params
    ? api.getTrainingObservabilitySummary(jobId, params)
    : api.getTrainingObservabilitySummary(jobId);
}

function loadAttemptAnalysis(jobId: string) {
  const params = selectedAttemptParams();
  return params
    ? api.getTrainingObservabilityAnalysis(jobId, params)
    : api.getTrainingObservabilityAnalysis(jobId);
}

function loadAttemptArtifacts(jobId: string) {
  const params = selectedAttemptParams();
  return params
    ? api.getTrainingObservabilityArtifacts(jobId, params)
    : api.getTrainingObservabilityArtifacts(jobId);
}

function formatImageDigest(value: string | undefined) {
  if (!value) return "-";
  const marker = value.indexOf("sha256:");
  return marker >= 0 ? value.slice(marker) : value;
}

function formatArtifactSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function openExternalTool(url: string) {
  advancedMenuOpen.value = false;
  window.open(url, "_blank", "noopener,noreferrer");
}

function sourceLabel(name: string) {
  return ({ mlflow: "MLflow", tensorboard: "TensorBoard", progress: "训练进度", artifacts: "训练产物" } as Record<string, string>)[name] || name;
}

function hasPoints(series: Record<string, Array<unknown>>) {
  return Object.values(series).some((points) => points.length > 0);
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function clearPollTimer() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function schedulePoll(status: string, requestGeneration: number) {
  clearPollTimer();
  if (requestGeneration !== generation || !ACTIVE_STATUSES.has(status.toLowerCase())) return;
  pollTimer = setTimeout(() => {
    pollTimer = null;
    if (requestGeneration === generation) void loadSummary(requestGeneration);
  }, POLL_INTERVAL_MS);
}

function resetSelectedData(attemptId = selectedJob.value?.attempts?.[0]?.id ?? "") {
  summaryData.value = null;
  scalarSeries.value = {};
  resourceSeries.value = {};
  scalarAvailability.value = {};
  resourceAvailability.value = {};
  llmAnalysis.value = { findings: [], availability: {} };
  llmArtifacts.value = { items: [], availability: {} };
  metricsLoadedJobId = null;
  resourcesLoadedJobId = null;
  analysisLoadedJobId = null;
  artifactsLoadedJobId = null;
  selectedAttemptId.value = attemptId;
}

function externalActionLabel(source: string) {
  return ({ mlflow: "MLflow", tensorboard: "TensorBoard" } as Record<string, string>)[source] || source;
}

function isActiveStatus(status: string) {
  return ACTIVE_STATUSES.has(status.toLowerCase());
}

function canDeleteRun(job: TrainingJobRecord) {
  const status = job.status.toLowerCase();
  if (!ACTIVE_STATUSES.has(status)) return true;
  return status === "queued"
    && !job.task_id
    && !job.distributed_run_id
    && !job.remote_execution_id;
}

async function deleteRun(job: TrainingJobRecord) {
  try {
    await ElMessageBox.confirm(
      "将删除本次训练记录、日志和结果图，已经产出的可部署模型权重会保留。",
      "删除训练记录",
      { confirmButtonText: "删除", cancelButtonText: "取消", type: "warning" },
    );
    await api.deleteTrainingJob(job.id);
    if (selectedJob.value?.id === job.id) selectedJob.value = null;
    ElMessage.success("训练记录已删除");
    await loadRuns();
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof Error ? error.message : "训练记录删除失败");
    }
  }
}

async function loadScalars(requestGeneration: number, force = false) {
  const job = selectedJob.value;
  const key = job ? observationKey(job) : null;
  const advertisedKeys = [...new Set(summaryData.value?.available_scalar_keys.filter((key) => key.trim()) ?? [])];
  if (!job || requestGeneration !== generation) return;
  if (!force && metricsLoadedJobId === key) return;
  if (advertisedKeys.length === 0) {
    scalarSeries.value = {};
    scalarAvailability.value = summaryData.value?.availability ?? {};
    metricsLoadedJobId = key;
    return;
  }

  metricsLoading.value = true;
  try {
    const response = await api.getTrainingObservabilityScalars(job.id, {
      keys: advertisedKeys,
      max_points: 1000,
      ...(selectedAttemptParams() ?? {}),
    });
    if (requestGeneration !== generation || selectedJob.value?.id !== job.id) return;
    scalarSeries.value = response.series;
    scalarAvailability.value = response.availability;
    metricsLoadedJobId = key;
    if (activeTab.value === "metrics" && hasPoints(response.series)) metricsChartMounted.value = true;
  } catch (error) {
    if (requestGeneration === generation) {
      ElMessage.error(error instanceof Error ? error.message : "训练指标加载失败");
    }
  } finally {
    if (requestGeneration === generation) metricsLoading.value = false;
  }
}

async function loadResources(requestGeneration: number, force = false) {
  const job = selectedJob.value;
  const key = job ? observationKey(job) : null;
  if (!job || requestGeneration !== generation) return;
  if (!force && resourcesLoadedJobId === key) return;

  resourcesLoading.value = true;
  try {
    const response = await api.getTrainingObservabilityResources(job.id, {
      max_points: 1000,
      ...(selectedAttemptParams() ?? {}),
    });
    if (requestGeneration !== generation || selectedJob.value?.id !== job.id) return;
    resourceSeries.value = response.series;
    resourceAvailability.value = response.availability;
    resourcesLoadedJobId = key;
    if (activeTab.value === "resources" && hasPoints(response.series)) resourcesChartMounted.value = true;
  } catch (error) {
    if (requestGeneration === generation) {
      ElMessage.error(error instanceof Error ? error.message : "资源数据加载失败");
    }
  } finally {
    if (requestGeneration === generation) resourcesLoading.value = false;
  }
}

async function loadAnalysis(requestGeneration: number, force = false) {
  const job = selectedJob.value;
  const key = job ? observationKey(job) : null;
  if (!job || requestGeneration !== generation) return;
  if (!force && analysisLoadedJobId === key) return;
  try {
    const analysis = await loadAttemptAnalysis(job.id);
    if (requestGeneration !== generation || selectedJob.value?.id !== job.id) return;
    llmAnalysis.value = analysis;
    analysisLoadedJobId = key;
  } catch (error) {
    if (requestGeneration === generation) {
      ElMessage.error(error instanceof Error ? error.message : "训练分析加载失败");
    }
  }
}

async function loadObservabilityArtifacts(requestGeneration: number, force = false) {
  const job = selectedJob.value;
  const key = job ? observationKey(job) : null;
  if (!job || requestGeneration !== generation) return;
  if (!force && artifactsLoadedJobId === key) return;
  artifactsLoading.value = true;
  try {
    const artifacts = await loadAttemptArtifacts(job.id);
    if (requestGeneration !== generation || selectedJob.value?.id !== job.id) return;
    llmArtifacts.value = artifacts;
    artifactsLoadedJobId = key;
  } catch (error) {
    if (requestGeneration === generation) {
      ElMessage.error(error instanceof Error ? error.message : "训练产物加载失败");
    }
  } finally {
    if (requestGeneration === generation) artifactsLoading.value = false;
  }
}

function openObservabilityArtifact(artifact: LlmArtifact) {
  if (!artifact.download_url) {
    ElMessage.warning("该历史制品没有可用的下载地址");
    return;
  }
  window.open(artifact.download_url, "_blank", "noopener,noreferrer");
}

async function loadActiveTabData(requestGeneration: number, force = false) {
  if (activeTab.value === "metrics") await loadScalars(requestGeneration, force);
  if (activeTab.value === "resources") await loadResources(requestGeneration, force);
  if (activeTab.value === "analysis") {
    if (isPaddlexRun.value) await loadScalars(requestGeneration, force);
    await Promise.all([
      loadAnalysis(requestGeneration, force),
      loadObservabilityArtifacts(requestGeneration, force),
    ]);
  }
  if (activeTab.value === "artifacts") await loadObservabilityArtifacts(requestGeneration, force);
}

async function loadSummary(requestGeneration: number, loadActiveTab = true) {
  const job = selectedJob.value;
  if (!job || requestGeneration !== generation) return;
  summaryLoading.value = true;
  try {
    const response = await loadAttemptSummary(job.id);
    if (requestGeneration !== generation || selectedJob.value?.id !== job.id) return;

    summaryData.value = response;
    const updatedJob = { ...job, status: response.status };
    selectedJob.value = updatedJob;
    jobs.value = jobs.value.map((item) => item.id === job.id ? updatedJob : item);
    schedulePoll(response.status, requestGeneration);
    if (loadActiveTab) await loadActiveTabData(requestGeneration, true);
  } catch (error) {
    if (requestGeneration === generation) {
      ElMessage.error(error instanceof Error ? error.message : "训练概览加载失败");
      schedulePoll(currentStatus.value, requestGeneration);
    }
  } finally {
    if (requestGeneration === generation) summaryLoading.value = false;
  }
}

async function selectJob(job: TrainingJobRecord) {
  if (selectedJob.value?.id === job.id && summaryData.value) return;
  const requestGeneration = ++generation;
  clearPollTimer();
  selectedJob.value = job;
  resetSelectedData();
  await loadSummary(requestGeneration);
}

async function selectAttempt() {
  const job = selectedJob.value;
  if (!job) return;
  const requestGeneration = ++generation;
  clearPollTimer();
  resetSelectedData(selectedAttemptId.value);
  await loadSummary(requestGeneration, false);
  await Promise.all([
    loadScalars(requestGeneration, true),
    loadResources(requestGeneration, true),
    loadAnalysis(requestGeneration, true),
    loadObservabilityArtifacts(requestGeneration, true),
  ]);
}

async function activateTab(tab: DashboardTab) {
  activeTab.value = tab;
  if (tab === "metrics") {
    metricsActivated.value = true;
    if (hasMetricSeries.value) metricsChartMounted.value = true;
  }
  if (tab === "resources") {
    resourcesActivated.value = true;
    if (hasResourceSeries.value) resourcesChartMounted.value = true;
  }
  if (tab === "analysis") analysisActivated.value = true;
  if (tab === "artifacts") artifactsActivated.value = true;
  await loadActiveTabData(generation);
}

async function loadRuns() {
  const requestGeneration = ++generation;
  clearPollTimer();
  listLoading.value = true;
  try {
    const [pipelineResponse, jobResponse] = await Promise.all([
      api.listPipelines({ limit: 200 }),
      api.listTrainingJobs({ limit: 200 }),
    ]);
    if (requestGeneration !== generation) return;

    pipelines.value = pipelineResponse.items;
    jobs.value = [...jobResponse.items].sort((left, right) =>
      String(right.created_at || "").localeCompare(String(left.created_at || "")),
    );
    const routeJobId = Array.isArray(route.query.job) ? route.query.job[0] : route.query.job;
    const selectedId = typeof routeJobId === "string" && routeJobId ? routeJobId : selectedJob.value?.id;
    selectedJob.value = jobs.value.find((job) => job.id === selectedId) ?? jobs.value[0] ?? null;
    resetSelectedData();
    listLoading.value = false;
    if (selectedJob.value) await loadSummary(requestGeneration);
  } catch (error) {
    if (requestGeneration === generation) {
      ElMessage.error(error instanceof Error ? error.message : "训练记录加载失败");
    }
  } finally {
    if (requestGeneration === generation) listLoading.value = false;
  }
}

onMounted(loadRuns);
onBeforeUnmount(() => {
  generation += 1;
  clearPollTimer();
});
</script>

<style scoped>
.training-visualization-view { container: training-view / inline-size; min-width: 0; color: #1f2937; }
.training-visualization-view, .training-visualization-view * { box-sizing: border-box; }
.visualization-page-header { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 16px; }
.visualization-page-header > div:first-child { min-width: 0; }
.visualization-page-header h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
.visualization-page-header p { margin: 5px 0 0; overflow-wrap: anywhere; color: #667085; font-size: 14px; }
.header-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; }
.refresh-button { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 6px; height: 36px; padding: 0 14px; border: 1px solid #cfd7e6; border-radius: 4px; background: #fff; color: #344054; white-space: nowrap; cursor: pointer; }
.refresh-button:disabled { cursor: wait; opacity: .55; }
.advanced-menu-wrap { position: relative; }
.icon-button { display: grid; width: 36px; height: 36px; padding: 0; place-items: center; border: 1px solid #cfd7e6; border-radius: 4px; background: #fff; color: #344054; cursor: pointer; }
.icon-button:hover { border-color: #2563eb; color: #1d4ed8; }
.advanced-menu { position: absolute; z-index: 20; top: 42px; right: 0; display: grid; width: 168px; padding: 5px; border: 1px solid #d7deea; border-radius: 6px; box-shadow: 0 10px 24px rgb(15 23 42 / 14%); background: #fff; }
.advanced-menu button { height: 34px; padding: 0 10px; border: 0; border-radius: 4px; background: transparent; color: #344054; text-align: left; cursor: pointer; }
.advanced-menu button:hover { background: #eef4ff; color: #1d4ed8; }
.advanced-menu span { padding: 9px 10px; color: #98a2b3; font-size: 12px; }
.visualization-layout { display: grid; grid-template-columns: 288px minmax(0, 1fr); width: 100%; max-width: 100%; min-height: calc(100vh - 148px); border: 1px solid #dfe5ef; background: #fff; }
.run-list-panel { min-width: 0; border-right: 1px solid #dfe5ef; background: #f8fafc; }
.run-list-heading { display: flex; align-items: center; justify-content: space-between; height: 52px; padding: 0 16px; border-bottom: 1px solid #dfe5ef; }
.run-list-heading span { color: #667085; font-size: 13px; }
.run-list-state { padding: 24px 16px; color: #667085; }
.run-row { position: relative; border-bottom: 1px solid #e8edf5; }
.run-row:hover { background: #f1f5f9; }
.run-row.active { box-shadow: inset 3px 0 #2563eb; background: #eef4ff; }
.run-item { display: grid; width: 100%; gap: 9px; padding: 14px 48px 14px 16px; border: 0; background: transparent; text-align: left; cursor: pointer; }
.delete-run-button { position: absolute; top: 10px; right: 10px; display: grid; width: 30px; height: 30px; padding: 0; place-items: center; border: 0; border-radius: 4px; background: transparent; color: #667085; cursor: pointer; }
.delete-run-button:hover:not(:disabled) { background: #fee2e2; color: #dc2626; }
.delete-run-button:disabled { cursor: not-allowed; opacity: .35; }
.run-title { overflow: hidden; color: #172033; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.run-meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: #667085; font-size: 12px; }
.run-item code, .selected-run-band code { overflow: hidden; color: #475467; font-family: Consolas, monospace; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.visualization-workspace { width: 100%; max-width: 100%; min-width: 0; padding: 0; }
.selected-run-band { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(180px, auto) auto; align-items: center; gap: 20px; min-height: 58px; padding: 0 18px; border-bottom: 1px solid #dfe5ef; background: #f8fafc; }
.selected-run-band > div { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.selected-run-band > div strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.band-label { color: #667085; font-size: 12px; }
.dashboard-tabs { display: flex; min-width: 0; overflow-x: auto; border-bottom: 1px solid #dfe5ef; background: #fff; }
.dashboard-tabs button { flex: 0 0 auto; height: 44px; padding: 0 20px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: #667085; cursor: pointer; }
.dashboard-tabs button:hover { color: #1d4ed8; }
.dashboard-tabs button.active { border-bottom-color: #2563eb; color: #1d4ed8; font-weight: 600; }
.dashboard-panels { width: 100%; max-width: 100%; min-width: 0; }
.dashboard-panel { width: 100%; max-width: 100%; min-width: 0; }
.panel-loading { display: grid; min-height: 180px; place-items: center; color: #667085; }
.overview-band { border-bottom: 1px solid #e3e8ef; }
.progress-band { display: grid; grid-template-columns: 180px minmax(260px, 1fr); align-items: center; gap: 24px; padding: 20px 22px; background: #fbfcfe; }
.status-cell, .progress-cell { min-width: 0; }
.status-cell { display: grid; gap: 6px; }
.status-cell span, .progress-heading span, .metric-cell span { color: #667085; font-size: 12px; }
.status-cell strong { font-size: 18px; }
.progress-heading { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 8px; }
.metric-grid { display: grid; }
.detail-grid { grid-template-columns: repeat(7, minmax(0, 1fr)); }
.metric-cell { display: grid; min-width: 0; gap: 7px; padding: 16px 18px; border-right: 1px solid #e3e8ef; }
.metric-cell:last-child { border-right: 0; }
.metric-cell strong { overflow-wrap: anywhere; font-size: 15px; font-variant-numeric: tabular-nums; }
.band-title { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding: 15px 18px 9px; }
.band-title span, .panel-heading span { color: #667085; font-size: 12px; }
.latest-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.latest-grid .metric-cell strong { font-size: 18px; }
.source-list { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.source-item { display: grid; grid-template-columns: 9px minmax(0, 1fr) auto; align-items: center; gap: 8px; min-width: 0; min-height: 52px; padding: 0 18px; border-right: 1px solid #e3e8ef; }
.source-item:last-child { border-right: 0; }
.source-item strong { min-width: 0; overflow-wrap: anywhere; }
.source-item > span:not(.source-dot) { justify-self: end; color: #667085; font-size: 12px; white-space: nowrap; }
.source-item small { grid-column: 2 / -1; overflow: hidden; color: #b42318; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.source-dot { width: 8px; height: 8px; border-radius: 50%; background: #ef4444; }
.source-dot.available { background: #16a34a; }
.provenance-band { border-bottom: 1px solid #e3e8ef; background: #f8fafc; }
.provenance-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; padding: 14px 18px 8px; }
.provenance-heading span { color: #667085; font-size: 12px; }
.provenance-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); }
.provenance-cell { display: grid; min-width: 0; gap: 5px; padding: 11px 18px 14px; border-right: 1px solid #e3e8ef; }
.provenance-cell:last-child { border-right: 0; }
.provenance-cell span { color: #667085; font-size: 11px; }
.provenance-cell strong, .provenance-cell code { overflow: hidden; color: #172033; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.provenance-cell code { font-family: Consolas, monospace; }
.attempt-cell select { width: 100%; height: 28px; border: 1px solid #cfd7e6; border-radius: 4px; background: #fff; color: #344054; font-size: 12px; }
.chart-panel { padding: 0 18px 20px; }
.panel-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 58px; border-bottom: 1px solid #e3e8ef; }
.panel-heading > div { display: flex; min-width: 0; align-items: baseline; gap: 10px; }
.metrics-toolbar { display: flex; min-height: 58px; align-items: center; justify-content: space-between; gap: 20px; }
.metrics-mode-switch { display: inline-flex; border: 1px solid #d5dce7; border-radius: 4px; overflow: hidden; }
.metrics-mode-switch button { min-width: 92px; height: 34px; padding: 0 14px; border: 0; border-right: 1px solid #d5dce7; background: #fff; color: #475467; cursor: pointer; }
.metrics-mode-switch button:last-child { border-right: 0; }
.metrics-mode-switch button.active { background: #eef4ff; color: #1769ff; font-weight: 600; }
.smoothing-control { display: flex; align-items: center; gap: 9px; color: #475467; font-size: 12px; }
.smoothing-control input { width: 120px; accent-color: #1769ff; }
.smoothing-control output { width: 26px; color: #101828; font-variant-numeric: tabular-nums; }
.metric-card-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.artifact-dashboard-panel { padding: 18px; }
.observability-artifact-list { border: 1px solid #dfe5ed; background: #fff; }
.observability-artifact-list button { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 14px; padding: 12px 14px; border: 0; border-bottom: 1px solid #edf0f4; background: #fff; color: #172033; text-align: left; cursor: pointer; }
.observability-artifact-list button:last-child { border-bottom: 0; }
.observability-artifact-list button:hover { background: #f7f9fc; }
.observability-artifact-list button > span:first-child { display: grid; min-width: 0; gap: 3px; }
.observability-artifact-list strong, .observability-artifact-list small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.observability-artifact-list small, .observability-artifact-list button > span:last-child { color: #667085; font-size: 11px; }
.source-warning { color: #b54708 !important; }
.placeholder-panel { min-height: 420px; padding-top: 80px; }
@container training-view (max-width: 900px) {
  .visualization-layout { grid-template-columns: 240px minmax(0, 1fr); }
  .detail-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .detail-grid .metric-cell { border-bottom: 1px solid #e3e8ef; }
  .source-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .provenance-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .source-item { border-bottom: 1px solid #e3e8ef; }
}
@container training-view (max-width: 760px) {
  .visualization-page-header { align-items: flex-start; }
  .visualization-layout { display: block; }
  .run-list-panel { max-height: 280px; overflow-y: auto; border-right: 0; border-bottom: 1px solid #dfe5ef; }
  .selected-run-band { grid-template-columns: minmax(0, 1fr) auto; padding: 10px 14px; }
  .selected-run-band code { grid-column: 1 / -1; }
  .progress-band { grid-template-columns: 1fr; }
  .detail-grid, .latest-grid, .source-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .provenance-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-cell:nth-child(2n), .source-item:nth-child(2n) { border-right: 0; }
  .metric-card-grid { grid-template-columns: minmax(0, 1fr); }
}
@container training-view (max-width: 420px) {
  .visualization-page-header { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; }
  .visualization-page-header h1 { font-size: 21px; }
  .selected-run-band > div { flex-wrap: wrap; gap: 4px 8px; }
  .dashboard-tabs button { padding: 0 14px; }
  .band-title { flex-wrap: wrap; }
  .source-list { grid-template-columns: minmax(0, 1fr); }
  .provenance-grid { grid-template-columns: minmax(0, 1fr); }
  .provenance-cell { border-right: 0; border-bottom: 1px solid #e3e8ef; }
  .source-item { min-height: 56px; padding: 9px 14px; border-right: 0; }
  .chart-panel { padding-right: 10px; padding-left: 10px; }
  .panel-heading { flex-direction: column; align-items: flex-start; gap: 4px; padding: 10px 0; }
  .metrics-toolbar { align-items: flex-start; flex-direction: column; gap: 10px; padding: 12px 0; }
  .smoothing-control { width: 100%; }
  .smoothing-control input { flex: 1; min-width: 0; }
}
</style>
