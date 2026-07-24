<template>
  <section class="workbench-view">
    <header class="workbench-title">
      <h1>工作台</h1>
    </header>

    <section class="panel data-panel">
      <div class="panel-heading">
        <h2>数据准备分析</h2>
        <button type="button" class="quick-link" @click="goDataPreparation">快速访问›</button>
      </div>

      <div class="data-grid">
        <div class="data-chart-card">
          <div class="metric-strip">
            <div v-for="metric in dataMetrics" :key="metric.label">
              <span>{{ metric.label }}</span>
              <strong :style="{ color: metric.color }">{{ metric.value }}</strong>
            </div>
          </div>
          <div class="pie-row">
            <div v-if="loading" class="pie-chart pie-chart-loading" aria-hidden="true"></div>
            <div v-else-if="pieSegments.length === 0" class="pie-chart pie-chart-loading" aria-label="暂无数据导入记录"></div>
            <div
              v-else
              class="pie-chart pie-chart-animated"
              role="group"
              aria-label="数据导入类型占比"
              @mouseleave="activePieIndex = null"
            >
              <svg viewBox="0 0 220 220">
                <g
                  v-for="segment in pieSegments"
                  :key="segment.index"
                  class="pie-sector-group"
                  :class="{ active: activePieIndex === segment.index }"
                  :style="{
                    '--segment-index': segment.order,
                  }"
                >
                  <path
                    class="pie-sector"
                    :d="segment.path"
                    :fill="segment.color"
                    role="button"
                    tabindex="0"
                    :aria-label="`${segment.label} ${segment.value}`"
                    @mouseenter="activePieIndex = segment.index"
                    @focus="activePieIndex = segment.index"
                    @blur="activePieIndex = null"
                    @click="togglePieSegment(segment.index)"
                    @keydown.enter.prevent="togglePieSegment(segment.index)"
                    @keydown.space.prevent="togglePieSegment(segment.index)"
                  >
                    <title>{{ segment.label }}：{{ segment.value }}</title>
                  </path>
                </g>
              </svg>
              <div
                v-if="activePieSegment"
                class="pie-tooltip"
                role="tooltip"
                :style="{ left: `${activePieSegment.tooltipX}%`, top: `${activePieSegment.tooltipY}%` }"
              >
                <i :style="{ background: activePieSegment.color }"></i>
                <span>{{ activePieSegment.label }}</span>
                <strong>{{ activePieSegment.value }}</strong>
              </div>
            </div>
            <ul class="pie-legend">
              <li v-for="metric in dataMetrics" :key="metric.label">
                <i :style="{ background: metric.color }"></i>
                <span>{{ metric.label }}</span>
              </li>
            </ul>
          </div>
        </div>

        <div class="tag-card">
          <h3>数据准备标签</h3>
          <div v-for="tag in dataTags" :key="tag.name" class="progress-row">
            <span>{{ tag.name }}</span>
            <div class="progress-track">
              <i :style="{ width: `${tag.percent}%` }"></i>
            </div>
            <strong>{{ tag.count }}个</strong>
          </div>
        </div>

        <div class="dataset-list">
          <article v-for="dataset in recentDatasets" :key="dataset.id" class="dataset-card">
            <h3>{{ dataset.name }}</h3>
            <div class="dataset-badges">
              <span :class="{ success: dataset.status === 'validated' }">{{ datasetStatusLabel(dataset.status) }}</span>
              <span class="imported">{{ datasetSourceLabel(dataset.source) }}</span>
              <span>{{ taskLabel(dataset.task) }}</span>
            </div>
            <p>{{ formatDate(dataset.created_at) }} <button type="button" class="dataset-link" @click="goDataPreparation">Label Studio</button></p>
          </article>
          <p v-if="!loading && recentDatasets.length === 0" class="empty-state">暂无数据集</p>
        </div>
      </div>
    </section>

    <div class="lower-grid">
      <section class="panel model-panel">
        <div class="panel-heading">
          <h2>模型空间分析</h2>
          <button type="button" class="quick-link" @click="goDataPreparation">快速访问›</button>
        </div>
        <div class="model-content">
          <div class="status-cards">
            <div v-for="status in modelStatuses" :key="status.label" class="status-card">
              <span :style="{ color: status.color }">●</span>
              <em>{{ status.label }}</em>
              <strong :style="{ color: status.color }">{{ status.value }}</strong>
            </div>
          </div>
          <div class="pipeline-bars">
            <div v-for="pipeline in pipelineStats" :key="pipeline.name" class="pipeline-row">
              <span>{{ pipeline.name }}</span>
              <div class="pipeline-track">
                <i :style="{ width: `${pipeline.percent}%` }"></i>
              </div>
              <strong>{{ pipeline.percent }}%</strong>
            </div>
          </div>
        </div>
      </section>

      <section class="panel services-panel">
        <div class="panel-heading">
          <h2>服务列表分析</h2>
          <button type="button" class="quick-link" @click="goServices">快速访问›</button>
        </div>
        <div class="service-table">
          <div v-for="service in recentServices" :key="service.id" class="service-row">
            <span>{{ service.name }}</span>
            <time>{{ formatDate(service.created_at) }}</time>
            <span>{{ pipelineName(service.pipeline_id) }}</span>
            <strong :class="service.status">{{ serviceStatusLabel(service.status) }}</strong>
          </div>
          <p v-if="!loading && recentServices.length === 0" class="empty-state">暂无已部署服务</p>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import {
  api,
  type DatasetRecord,
  type DeploymentServiceRecord,
  type TrainingPipelineRecord,
} from "@/api/client";

const router = useRouter();
const loading = ref(true);
const datasets = ref<DatasetRecord[]>([]);
const pipelines = ref<TrainingPipelineRecord[]>([]);
const services = ref<DeploymentServiceRecord[]>([]);
const activePieIndex = ref<number | null>(null);

const taskNames: Record<string, string> = {
  detect: "目标检测",
  classify: "图像分类",
  segment: "实例分割",
  pose: "关键点检测",
  obb: "旋转框检测",
  semantic: "语义分割",
  document: "文档信息抽取",
  ocr: "OCR",
  table: "表格识别",
  llm: "大模型训练",
};

const dataMetrics = computed(() => {
  const isVideo = (dataset: DatasetRecord) => (dataset.source || "").toLowerCase().includes("video");
  const video = datasets.value.filter(isVideo).length;
  const annotated = datasets.value.filter((dataset) => dataset.annotation_count > 0 && !isVideo(dataset)).length;
  const unannotated = datasets.value.length - annotated - video;
  return [
    { label: "已标注数据导入", value: annotated, color: "#16a34a" },
    { label: "未标注数据导入", value: Math.max(0, unannotated), color: "#8b35eb" },
    { label: "视频文件导入", value: video, color: "#f59e0b" },
  ];
});

const pieSegments = computed(() => {
  const total = dataMetrics.value.reduce((sum, metric) => sum + metric.value, 0);
  if (total === 0) return [];

  let cursor = -90;
  return dataMetrics.value.flatMap((metric, index) => {
    if (metric.value <= 0) return [];
    const sweep = (metric.value / total) * 360;
    const startAngle = cursor;
    const endAngle = cursor + sweep;
    const middleAngle = startAngle + sweep / 2;
    cursor = endAngle;
    const middleRadians = (middleAngle * Math.PI) / 180;
    const tooltipRadius = 72;
    return [{
      ...metric,
      index,
      order: index,
      path: pieSectorPath(startAngle, endAngle),
      tooltipX: clamp(((110 + Math.cos(middleRadians) * tooltipRadius) / 220) * 100, 18, 82),
      tooltipY: clamp(((110 + Math.sin(middleRadians) * tooltipRadius) / 220) * 100, 14, 86),
    }];
  });
});

const activePieSegment = computed(() =>
  pieSegments.value.find((segment) => segment.index === activePieIndex.value) ?? null,
);

function pieSectorPath(startAngle: number, endAngle: number) {
  const center = 110;
  const radius = 92;
  const sweep = endAngle - startAngle;
  if (sweep >= 359.999) {
    return `M ${center} ${center - radius} A ${radius} ${radius} 0 1 1 ${center} ${center + radius} A ${radius} ${radius} 0 1 1 ${center} ${center - radius} Z`;
  }
  const start = polarPoint(center, center, radius, startAngle);
  const end = polarPoint(center, center, radius, endAngle);
  const largeArc = sweep > 180 ? 1 : 0;
  return `M ${center} ${center} L ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y} Z`;
}

function polarPoint(centerX: number, centerY: number, radius: number, angle: number) {
  const radians = (angle * Math.PI) / 180;
  return {
    x: centerX + Math.cos(radians) * radius,
    y: centerY + Math.sin(radians) * radius,
  };
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function togglePieSegment(index: number) {
  activePieIndex.value = activePieIndex.value === index ? null : index;
}

const dataTags = computed(() => {
  const counts = new Map<string, number>();
  datasets.value.forEach((dataset) => {
    const label = taskLabel(dataset.task);
    counts.set(label, (counts.get(label) || 0) + 1);
  });
  const max = Math.max(1, ...counts.values());
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count, percent: Math.round((count / max) * 100) }))
    .sort((left, right) => right.count - left.count)
    .slice(0, 5);
});

const recentDatasets = computed(() =>
  [...datasets.value]
    .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")))
    .slice(0, 3),
);

const modelStatuses = computed(() => {
  const groups = [
    { label: "运行中止", statuses: ["stopped", "failed", "aborted", "canceled"], color: "#dc2626" },
    { label: "运行成功", statuses: ["success"], color: "#16a34a" },
    { label: "训练中", statuses: ["running", "training"], color: "#0ea5e9" },
    { label: "配置中", statuses: ["draft", "ready", "configuring"], color: "#8b35eb" },
  ];
  return groups.map((group) => ({
    label: group.label,
    color: group.color,
    value: pipelines.value.filter((pipeline) => group.statuses.includes(pipeline.status)).length,
  }));
});

const pipelineStats = computed(() => {
  const counts = new Map<string, number>();
  pipelines.value.forEach((pipeline) => {
    const label = taskLabel(pipeline.task);
    counts.set(label, (counts.get(label) || 0) + 1);
  });
  const total = Math.max(1, pipelines.value.length);
  return [...counts.entries()]
    .map(([name, count]) => ({ name, percent: Math.round((count / total) * 100) }))
    .sort((left, right) => right.percent - left.percent)
    .slice(0, 4);
});

const recentServices = computed(() =>
  [...services.value].sort((left, right) => right.created_at.localeCompare(left.created_at)).slice(0, 5),
);

onMounted(async () => {
  loading.value = true;
  try {
    const [datasetResponse, pipelineResponse, serviceResponse] = await Promise.all([
      api.listDatasets(),
      api.listPipelines(),
      api.listServices({ limit: 200, offset: 0 }),
    ]);
    datasets.value = datasetResponse.items;
    pipelines.value = pipelineResponse.items;
    services.value = serviceResponse.items;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "工作台数据加载失败");
  } finally {
    loading.value = false;
  }
});

function taskLabel(task: string) {
  return taskNames[task] || task || "未知任务";
}

function datasetStatusLabel(status: string) {
  if (status === "validated") return "已校验";
  if (status === "processing") return "处理中";
  return "待校验";
}

function datasetSourceLabel(source?: string) {
  const value = (source || "").toLowerCase();
  if (value.includes("label")) return "Label Studio";
  if (value.includes("video")) return "视频导入";
  return "导入";
}

function pipelineName(pipelineId: string) {
  return pipelines.value.find((pipeline) => pipeline.id === pipelineId)?.name || pipelineId;
}

function serviceStatusLabel(status: string) {
  if (status === "running") return "运行中";
  if (status === "deploying") return "部署中";
  if (status === "failed") return "部署失败";
  return "已终止";
}

function formatDate(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function goDataPreparation() {
  void router.push("/data-preparation");
}

function goServices() {
  void router.push("/services");
}
</script>

<style scoped>
.workbench-view {
  container: workbench / inline-size;
  min-height: 100%;
  min-width: 0;
  color: #111827;
}

.workbench-title h1 {
  margin: 0 0 16px;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0;
}

.panel {
  border: 1px solid #e3e8f0;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 1px 2px rgb(15 23 42 / 4%), 0 8px 24px rgb(15 23 42 / 3%);
}

.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px 12px;
}

.panel-heading h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
}

.quick-link {
  border: 0;
  background: transparent;
  color: #1763ff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}

.data-grid {
  display: grid;
  grid-template-columns: minmax(420px, 1.25fr) minmax(250px, 0.72fr) minmax(250px, 0.72fr);
  gap: 14px;
  padding: 0 18px 18px;
}

.data-chart-card,
.tag-card,
.dataset-list {
  min-width: 0;
  min-height: 334px;
  border: 1px solid #e1e6ee;
  border-radius: 4px;
  background: #fff;
}

.data-chart-card {
  grid-row: auto;
}

.metric-strip {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin: 10px 10px 0;
  border: 1px solid #e4e8ef;
  background: #f8fafc;
}

.metric-strip div {
  display: grid;
  gap: 4px;
  justify-items: center;
  padding: 13px 8px 11px;
  font-size: 14px;
}

.metric-strip strong {
  font-size: 16px;
  font-weight: 500;
}

.pie-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: clamp(28px, 6cqw, 74px);
  min-height: 250px;
  padding: 18px;
}

.pie-chart {
  position: relative;
  width: clamp(168px, 21cqw, 218px);
  aspect-ratio: 1;
  height: auto;
  flex: 0 0 auto;
}

.pie-chart-loading {
  border-radius: 50%;
  background: #edf0f5;
}

.pie-chart-animated svg {
  display: block;
  width: 100%;
  height: 100%;
  overflow: visible;
  animation: pie-reveal 900ms cubic-bezier(0.22, 1, 0.36, 1) both;
}

.pie-sector-group {
  transform-box: view-box;
  transform-origin: 110px 110px;
  transition: transform 180ms cubic-bezier(0.22, 1, 0.36, 1);
}

.pie-sector-group.active {
  transform: scale(1.065);
}

.pie-sector {
  stroke: #ffffff;
  stroke-width: 1.5;
  cursor: pointer;
  outline: none;
  transform-box: fill-box;
  transform-origin: center;
  animation: sector-enter 560ms cubic-bezier(0.22, 1, 0.36, 1) both;
  animation-delay: calc(var(--segment-index) * 70ms);
  transition: filter 180ms ease;
}

.pie-sector:hover,
.pie-sector:focus-visible {
  filter: drop-shadow(0 4px 5px rgb(15 23 42 / 20%));
}

.pie-sector:focus-visible {
  stroke: #172033;
  stroke-width: 2.5;
}

.pie-tooltip {
  position: absolute;
  z-index: 3;
  display: flex;
  min-height: 34px;
  align-items: center;
  gap: 7px;
  padding: 7px 10px;
  border: 1px solid #d8dee9;
  border-radius: 4px;
  background: #ffffff;
  box-shadow: 0 5px 14px rgb(15 23 42 / 16%);
  color: #475467;
  font-size: 12px;
  pointer-events: none;
  transform: translate(-50%, -50%);
  white-space: nowrap;
}

.pie-tooltip i {
  width: 9px;
  height: 9px;
  flex: 0 0 9px;
  border-radius: 50%;
}

.pie-tooltip strong {
  margin-left: 6px;
  color: #172033;
  font-weight: 600;
}

.pie-legend {
  display: grid;
  gap: 22px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
}

.pie-legend li {
  display: flex;
  align-items: center;
  gap: 14px;
}

.pie-legend i {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.tag-card {
  padding: 18px 16px 0;
}

.tag-card h3 {
  margin: 0 0 12px;
  color: #1763ff;
  font-size: 16px;
  font-weight: 500;
}

.progress-row {
  display: grid;
  grid-template-columns: minmax(72px, 100px) minmax(50px, 1fr) 38px;
  align-items: center;
  gap: 10px;
  min-height: 54px;
  border-bottom: 1px solid #edf0f4;
  font-size: 13px;
}

.progress-track {
  height: 8px;
  border-radius: 999px;
  background: #f1f2f4;
  overflow: hidden;
}

.pipeline-track {
  height: 14px;
  overflow: hidden;
  border-radius: 999px;
  background: #f1f2f4;
}

.progress-track i,
.pipeline-track i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: #2878ff;
  transform-origin: left center;
  animation: bar-grow 760ms cubic-bezier(0.22, 1, 0.36, 1) both;
}

@keyframes pie-reveal {
  from { transform: scale(0.92) rotate(-8deg); opacity: 0.35; }
  to { transform: scale(1) rotate(0); opacity: 1; }
}

@keyframes sector-enter {
  from { transform: scale(0.82); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}

@keyframes bar-grow {
  from { transform: scaleX(0); opacity: 0.35; }
  to { transform: scaleX(1); opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .pie-chart-animated svg,
  .pie-sector,
  .pie-sector-group,
  .progress-track i,
  .pipeline-track i {
    animation: none;
    transition: none;
  }
}

.progress-row strong {
  color: #1763ff;
  font-weight: 500;
}

.dataset-list {
  display: grid;
  align-content: start;
  gap: 8px;
  min-height: 0;
  padding: 10px;
  overflow: hidden;
}

.dataset-card {
  border: 1px solid #dfe5ee;
  border-radius: 4px;
  padding: 14px 14px 12px;
}

.dataset-card h3 {
  overflow: hidden;
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dataset-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.dataset-badges span,
.service-row strong {
  border-radius: 999px;
  padding: 4px 8px;
  background: #f2f4f7;
  font-size: 12px;
  font-weight: 500;
}

.dataset-badges .success {
  background: #dcfce7;
  color: #16a34a;
}

.dataset-badges .imported {
  background: #dcfce7;
  color: #16a34a;
}

.dataset-card p {
  margin: 0;
  color: #4b5563;
  font-size: 14px;
}

.dataset-link {
  margin-left: 8px;
  border: 0;
  background: transparent;
  color: #1763ff;
  cursor: pointer;
  padding: 0;
  text-decoration: none;
}

.lower-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(390px, 0.92fr);
  gap: 14px;
  margin-top: 14px;
}

.lower-grid > *,
.model-content,
.status-cards,
.pipeline-bars,
.service-table {
  min-width: 0;
}

.model-content {
  display: grid;
  grid-template-columns: minmax(150px, 184px) minmax(0, 1fr);
  gap: 24px;
  padding: 4px 18px 16px;
}

.status-cards {
  display: grid;
  gap: 12px;
}

.status-card {
  display: grid;
  grid-template-columns: 18px 1fr auto;
  align-items: center;
  min-height: 58px;
  padding: 0 14px;
  box-shadow: 0 6px 18px rgb(30 64 175 / 9%);
}

.status-card em {
  font-style: normal;
  font-size: 13px;
}

.status-card strong {
  font-size: 24px;
  font-weight: 600;
}

.pipeline-bars {
  display: grid;
  gap: 24px;
  padding: 8px 0 0;
}

.pipeline-row {
  display: grid;
  grid-template-columns: minmax(86px, 120px) minmax(60px, 1fr) 38px;
  align-items: center;
  gap: 14px;
  font-size: 13px;
}

.service-table {
  display: grid;
  gap: 6px;
  padding: 0 16px 16px;
}

.service-row {
  display: grid;
  grid-template-columns: minmax(100px, 1fr) minmax(132px, auto) minmax(80px, 1fr) 68px;
  align-items: center;
  min-height: 50px;
  padding: 0 10px;
  background: #f8f5ff;
  font-size: 13px;
}

.service-row > span,
.service-row > time,
.service-row > strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.service-row strong.deploying {
  background: #f1ddff;
  color: #8b35eb;
}

.service-row strong.stopped {
  background: #dbeafe;
  color: #1763ff;
}

.service-row strong.running {
  background: #dcfce7;
  color: #16a34a;
}

.service-row strong.failed {
  background: #fee2e2;
  color: #dc2626;
}

.empty-state {
  margin: 40px 0;
  color: #98a2b3;
  text-align: center;
}

@container workbench (max-width: 1040px) {
  .data-grid {
    grid-template-columns: minmax(0, 1fr) minmax(260px, 0.72fr);
  }

  .dataset-list {
    grid-column: 1 / -1;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .lower-grid {
    grid-template-columns: 1fr;
  }
}

@container workbench (max-width: 760px) {
  .data-grid,
  .lower-grid {
    grid-template-columns: 1fr;
  }

  .data-chart-card {
    grid-row: auto;
  }

  .dataset-list {
    grid-column: auto;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .service-table {
    overflow-x: auto;
  }

  .service-row {
    min-width: 620px;
  }
}

@container workbench (max-width: 520px) {
  .metric-strip {
    grid-template-columns: 1fr;
  }

  .metric-strip div {
    grid-template-columns: 1fr auto;
    justify-items: start;
  }

  .pie-row {
    flex-direction: column;
  }

  .pie-legend {
    grid-template-columns: 1fr;
    gap: 8px;
    width: 100%;
  }

  .dataset-list,
  .model-content {
    grid-template-columns: 1fr;
  }

  .service-table {
    max-width: 100%;
    overflow-x: auto;
  }
}
</style>
