<template>
  <section class="training-visualization-view">
    <header class="visualization-page-header">
      <div>
        <h1>可视化训练</h1>
        <p>集中查看训练进度、指标趋势和资源使用情况</p>
      </div>
      <button class="refresh-button" type="button" :disabled="listLoading" @click="loadRuns">
        <el-icon><Refresh /></el-icon>
        刷新
      </button>
    </header>

    <div class="visualization-layout">
      <aside class="run-list-panel">
        <div class="run-list-heading">
          <strong>训练记录</strong>
          <span>{{ jobs.length }} 条</span>
        </div>
        <div v-if="listLoading" class="run-list-state">正在加载训练记录...</div>
        <el-empty v-else-if="jobs.length === 0" description="暂无训练记录" />
        <button
          v-for="job in jobs"
          v-else
          :key="job.id"
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
            </section>

            <section
              v-if="metricsActivated"
              v-show="activeTab === 'metrics'"
              class="dashboard-panel chart-panel"
              data-testid="metrics-panel"
            >
              <div class="panel-heading">
                <div><strong>训练指标</strong><span>Epoch 标量趋势</span></div>
                <span v-if="metricsUnavailableText" class="source-warning">{{ metricsUnavailableText }}</span>
              </div>
              <div v-if="metricsLoading && !metricsChartMounted" class="panel-loading">正在加载指标...</div>
              <el-empty v-else-if="!metricsChartMounted" description="该训练暂无可用指标" />
              <MetricLineChart v-if="metricsChartMounted" :series="scalarSeries" height="420px" />
            </section>

            <section
              v-if="resourcesActivated"
              v-show="activeTab === 'resources'"
              class="dashboard-panel chart-panel"
              data-testid="resources-panel"
            >
              <div class="panel-heading">
                <div><strong>资源监控</strong><span>CPU、内存与显存趋势</span></div>
              </div>
              <div v-if="resourcesLoading && !resourcesChartMounted" class="panel-loading">正在加载资源数据...</div>
              <el-empty v-else-if="!resourcesChartMounted" description="该训练暂无资源记录" />
              <MetricLineChart v-if="resourcesChartMounted" :series="resourceSeries" height="420px" />
            </section>

            <section v-show="activeTab === 'analysis'" class="dashboard-panel placeholder-panel">
              <el-empty description="该训练暂无结果分析" />
            </section>
            <section v-show="activeTab === 'graph'" class="dashboard-panel placeholder-panel">
              <el-empty description="该训练尚未记录计算图" />
            </section>
            <section v-show="activeTab === 'histograms'" class="dashboard-panel placeholder-panel">
              <el-empty description="该训练尚未记录参数分布" />
            </section>
          </div>
        </template>
        <el-empty v-else description="请选择一条训练记录" />
      </main>
    </div>
  </section>
</template>

<script setup lang="ts">
import { Refresh } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import {
  api,
  type TrainingJobRecord,
  type TrainingObservabilityAvailability,
  type TrainingObservabilityResources,
  type TrainingObservabilityScalars,
  type TrainingObservabilitySummary,
  type TrainingPipelineRecord,
} from "@/api/client";
import MetricLineChart from "@/components/training/MetricLineChart.vue";

type DashboardTab = "overview" | "metrics" | "resources" | "analysis" | "graph" | "histograms";

const POLL_INTERVAL_MS = 5000;
const ACTIVE_STATUSES = new Set(["queued", "running"]);
const tabs: Array<{ id: DashboardTab; label: string }> = [
  { id: "overview", label: "概览" },
  { id: "metrics", label: "指标" },
  { id: "resources", label: "资源" },
  { id: "analysis", label: "分析" },
  { id: "graph", label: "计算图" },
  { id: "histograms", label: "直方图" },
];

const jobs = ref<TrainingJobRecord[]>([]);
const pipelines = ref<TrainingPipelineRecord[]>([]);
const selectedJob = ref<TrainingJobRecord | null>(null);
const summaryData = ref<TrainingObservabilitySummary | null>(null);
const scalarSeries = ref<TrainingObservabilityScalars["series"]>({});
const resourceSeries = ref<TrainingObservabilityResources["series"]>({});
const scalarAvailability = ref<TrainingObservabilityAvailability>({});
const activeTab = ref<DashboardTab>("overview");
const listLoading = ref(false);
const summaryLoading = ref(false);
const metricsLoading = ref(false);
const resourcesLoading = ref(false);
const metricsActivated = ref(false);
const resourcesActivated = ref(false);
const metricsChartMounted = ref(false);
const resourcesChartMounted = ref(false);
let metricsLoadedJobId: string | null = null;
let resourcesLoadedJobId: string | null = null;
let generation = 0;
let pollTimer: ReturnType<typeof setTimeout> | null = null;

const currentStatus = computed(() => summaryData.value?.status || selectedJob.value?.status || "queued");
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

function resetSelectedData() {
  summaryData.value = null;
  scalarSeries.value = {};
  resourceSeries.value = {};
  scalarAvailability.value = {};
  metricsLoadedJobId = null;
  resourcesLoadedJobId = null;
}

async function loadScalars(requestGeneration: number, force = false) {
  const job = selectedJob.value;
  const advertisedKeys = [...new Set(summaryData.value?.available_scalar_keys.filter((key) => key.trim()) ?? [])];
  if (!job || requestGeneration !== generation) return;
  if (!force && metricsLoadedJobId === job.id) return;
  if (advertisedKeys.length === 0) {
    scalarSeries.value = {};
    scalarAvailability.value = summaryData.value?.availability ?? {};
    metricsLoadedJobId = job.id;
    return;
  }

  metricsLoading.value = true;
  try {
    const response = await api.getTrainingObservabilityScalars(job.id, { keys: advertisedKeys, max_points: 1000 });
    if (requestGeneration !== generation || selectedJob.value?.id !== job.id) return;
    scalarSeries.value = response.series;
    scalarAvailability.value = response.availability;
    metricsLoadedJobId = job.id;
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
  if (!job || requestGeneration !== generation) return;
  if (!force && resourcesLoadedJobId === job.id) return;

  resourcesLoading.value = true;
  try {
    const response = await api.getTrainingObservabilityResources(job.id, { max_points: 1000 });
    if (requestGeneration !== generation || selectedJob.value?.id !== job.id) return;
    resourceSeries.value = response.series;
    resourcesLoadedJobId = job.id;
    if (activeTab.value === "resources" && hasPoints(response.series)) resourcesChartMounted.value = true;
  } catch (error) {
    if (requestGeneration === generation) {
      ElMessage.error(error instanceof Error ? error.message : "资源数据加载失败");
    }
  } finally {
    if (requestGeneration === generation) resourcesLoading.value = false;
  }
}

async function loadActiveTabData(requestGeneration: number, force = false) {
  if (activeTab.value === "metrics") await loadScalars(requestGeneration, force);
  if (activeTab.value === "resources") await loadResources(requestGeneration, force);
}

async function loadSummary(requestGeneration: number) {
  const job = selectedJob.value;
  if (!job || requestGeneration !== generation) return;
  summaryLoading.value = true;
  try {
    const response = await api.getTrainingObservabilitySummary(job.id);
    if (requestGeneration !== generation || selectedJob.value?.id !== job.id) return;

    summaryData.value = response;
    const updatedJob = { ...job, status: response.status };
    selectedJob.value = updatedJob;
    jobs.value = jobs.value.map((item) => item.id === job.id ? updatedJob : item);
    schedulePoll(response.status, requestGeneration);
    await loadActiveTabData(requestGeneration, true);
  } catch (error) {
    if (requestGeneration === generation) {
      clearPollTimer();
      ElMessage.error(error instanceof Error ? error.message : "训练概览加载失败");
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
  await loadActiveTabData(generation);
}

async function loadRuns() {
  const requestGeneration = ++generation;
  clearPollTimer();
  listLoading.value = true;
  try {
    const [pipelineResponse, jobResponse] = await Promise.all([
      api.listPipelines(),
      api.listTrainingJobs({ limit: 200 }),
    ]);
    if (requestGeneration !== generation) return;

    pipelines.value = pipelineResponse.items;
    jobs.value = [...jobResponse.items].sort((left, right) =>
      String(right.created_at || "").localeCompare(String(left.created_at || "")),
    );
    const selectedId = selectedJob.value?.id;
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
.visualization-page-header > div { min-width: 0; }
.visualization-page-header h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
.visualization-page-header p { margin: 5px 0 0; overflow-wrap: anywhere; color: #667085; font-size: 14px; }
.refresh-button { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 6px; height: 36px; padding: 0 14px; border: 1px solid #cfd7e6; border-radius: 4px; background: #fff; color: #344054; white-space: nowrap; cursor: pointer; }
.refresh-button:disabled { cursor: wait; opacity: .55; }
.visualization-layout { display: grid; grid-template-columns: 288px minmax(0, 1fr); width: 100%; max-width: 100%; min-height: calc(100vh - 148px); border: 1px solid #dfe5ef; background: #fff; }
.run-list-panel { min-width: 0; border-right: 1px solid #dfe5ef; background: #f8fafc; }
.run-list-heading { display: flex; align-items: center; justify-content: space-between; height: 52px; padding: 0 16px; border-bottom: 1px solid #dfe5ef; }
.run-list-heading span { color: #667085; font-size: 13px; }
.run-list-state { padding: 24px 16px; color: #667085; }
.run-item { display: grid; width: 100%; gap: 9px; padding: 14px 16px; border: 0; border-bottom: 1px solid #e8edf5; background: transparent; text-align: left; cursor: pointer; }
.run-item:hover { background: #f1f5f9; }
.run-item.active { box-shadow: inset 3px 0 #2563eb; background: #eef4ff; }
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
.chart-panel { padding: 0 18px 20px; }
.panel-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 58px; border-bottom: 1px solid #e3e8ef; }
.panel-heading > div { display: flex; min-width: 0; align-items: baseline; gap: 10px; }
.source-warning { color: #b54708 !important; }
.placeholder-panel { min-height: 420px; padding-top: 80px; }
@container training-view (max-width: 900px) {
  .visualization-layout { grid-template-columns: 240px minmax(0, 1fr); }
  .detail-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .detail-grid .metric-cell { border-bottom: 1px solid #e3e8ef; }
  .source-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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
  .metric-cell:nth-child(2n), .source-item:nth-child(2n) { border-right: 0; }
}
@container training-view (max-width: 420px) {
  .visualization-page-header { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; }
  .visualization-page-header h1 { font-size: 21px; }
  .selected-run-band > div { flex-wrap: wrap; gap: 4px 8px; }
  .dashboard-tabs button { padding: 0 14px; }
  .band-title { flex-wrap: wrap; }
  .source-list { grid-template-columns: minmax(0, 1fr); }
  .source-item { min-height: 56px; padding: 9px 14px; border-right: 0; }
  .chart-panel { padding-right: 10px; padding-left: 10px; }
  .panel-heading { flex-direction: column; align-items: flex-start; gap: 4px; padding: 10px 0; }
}
</style>
