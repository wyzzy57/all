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
            <div class="pie-chart" :style="{ background: dataPieBackground }" aria-label="数据导入类型占比"></div>
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

const dataPieBackground = computed(() => {
  const total = dataMetrics.value.reduce((sum, metric) => sum + metric.value, 0);
  if (total === 0) return "#edf0f5";
  let cursor = 0;
  const segments = dataMetrics.value.map((metric) => {
    const start = cursor;
    cursor += (metric.value / total) * 100;
    return `${metric.color} ${start}% ${cursor}%`;
  });
  return `conic-gradient(${segments.join(", ")})`;
});

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
  min-height: 100%;
  background: #f5f7fb;
  color: #111827;
}

.workbench-title h1 {
  margin: 0 0 14px;
  font-size: 26px;
  font-weight: 600;
}

.panel {
  border: 1px solid #e3e8f0;
  background: #fff;
  box-shadow: 0 6px 16px rgb(15 23 42 / 4%);
}

.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px 10px;
}

.panel-heading h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 500;
}

.quick-link {
  border: 0;
  background: transparent;
  color: #1763ff;
  cursor: pointer;
  font-size: 16px;
}

.data-grid {
  display: grid;
  grid-template-columns: minmax(480px, 1.45fr) minmax(320px, 0.95fr) minmax(280px, 0.62fr);
  gap: 18px;
  padding: 0 20px 12px;
}

.data-chart-card,
.tag-card,
.dataset-list {
  min-height: 412px;
  border: 1px solid #e1e6ee;
  background: #fff;
}

.metric-strip {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin: 10px;
  border: 1px solid #e4e8ef;
  background: #f8fafc;
}

.metric-strip div {
  display: grid;
  gap: 4px;
  justify-items: center;
  padding: 18px 8px 10px;
  font-size: 18px;
}

.metric-strip strong {
  font-size: 16px;
  font-weight: 500;
}

.pie-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 92px;
  min-height: 300px;
}

.pie-chart {
  width: 222px;
  height: 222px;
  border-radius: 50%;
  background: conic-gradient(#16a34a 0 29%, #8b35eb 29% 98%, #f59e0b 98% 100%);
}

.pie-legend {
  display: grid;
  gap: 30px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 16px;
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
  padding: 24px 20px 0;
}

.tag-card h3 {
  margin: 0 0 12px;
  color: #1763ff;
  font-size: 16px;
  font-weight: 500;
}

.progress-row {
  display: grid;
  grid-template-columns: 112px 1fr 42px;
  align-items: center;
  gap: 10px;
  min-height: 74px;
  border-bottom: 1px solid #edf0f4;
  font-size: 16px;
}

.progress-track,
.pipeline-track {
  height: 8px;
  border-radius: 999px;
  background: #f1f2f4;
  overflow: hidden;
}

.progress-track i,
.pipeline-track i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: #2878ff;
}

.progress-row strong {
  color: #1763ff;
  font-weight: 500;
}

.dataset-list {
  display: grid;
  gap: 12px;
  padding: 12px 14px;
  overflow: hidden;
}

.dataset-card {
  border: 1px solid #dfe5ee;
  border-radius: 4px;
  padding: 22px 20px 14px;
}

.dataset-card h3 {
  margin: 0 0 14px;
  font-size: 16px;
  font-weight: 600;
}

.dataset-badges {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.dataset-badges span,
.service-row strong {
  border-radius: 999px;
  padding: 6px 10px;
  background: #f2f4f7;
  font-size: 14px;
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
  grid-template-columns: minmax(560px, 1fr) minmax(430px, 0.65fr);
  gap: 20px;
  margin-top: 20px;
}

.model-content {
  display: grid;
  grid-template-columns: 204px 1fr;
  gap: 48px;
  padding: 4px 20px 10px;
}

.status-cards {
  display: grid;
  gap: 12px;
}

.status-card {
  display: grid;
  grid-template-columns: 18px 1fr auto;
  align-items: center;
  min-height: 78px;
  padding: 0 20px;
  box-shadow: 0 6px 18px rgb(30 64 175 / 9%);
}

.status-card em {
  font-style: normal;
  font-size: 18px;
}

.status-card strong {
  font-size: 36px;
  font-weight: 400;
}

.pipeline-bars {
  display: grid;
  gap: 34px;
  padding: 8px 0 0;
}

.pipeline-row {
  display: grid;
  grid-template-columns: 140px 1fr 38px;
  align-items: center;
  gap: 14px;
  font-size: 15px;
}

.service-table {
  display: grid;
  gap: 6px;
  padding: 0 16px 16px;
}

.service-row {
  display: grid;
  grid-template-columns: minmax(130px, 1fr) 170px minmax(80px, 1fr) 74px;
  align-items: center;
  min-height: 50px;
  padding: 0 10px;
  background: #f8f5ff;
  font-size: 16px;
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

@media (max-width: 1280px) {
  .data-grid,
  .lower-grid {
    grid-template-columns: 1fr;
  }
}
</style>
