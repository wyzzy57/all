<template>
  <section class="workbench-view">
    <header class="workbench-header">
      <div>
        <h1>工作台</h1>
        <p>当前账号可访问资源的实时概览</p>
      </div>
      <time v-if="overview" class="generated-at">统计生成于 {{ formatTimestamp(overview.generated_at) }}</time>
    </header>

    <AsyncState
      v-if="overviewLoading"
      state="loading"
      title="正在加载工作台统计..."
      test-id="workbench-loading"
    />

    <AsyncState
      v-else-if="overviewError"
      :state="overviewErrorState"
      :title="overviewError"
      retry-label="重新加载"
      @retry="loadOverview"
    />

    <AsyncState
      v-else-if="isEmpty"
      state="empty"
      title="暂无可访问的资源。创建或获授权后，统计会自动显示在这里。"
      test-id="workbench-empty"
    />

    <template v-else-if="overview">
      <StatisticSummaryStrip :items="summaryItems" />

      <section class="dashboard-section preparation-section">
        <div class="section-heading">
          <h2>数据准备分析</h2>
          <button type="button" class="quick-link" @click="goDataPreparation">快速访问</button>
        </div>
        <div class="dashboard-grid dashboard-grid-three">
          <DatasetTrendChart :trend="datasetTrend" title="数据集创建趋势" />
          <ResourceUsagePanel
            :usage="resourceUsage"
            :gpu-series="gpuUsage"
            :stale="resourceStale"
          />
          <section class="dataset-summary" aria-label="数据集状态">
            <h3>数据集状态</h3>
            <dl>
              <div v-for="bucket in datasetStatusBuckets" :key="bucket.label">
                <dt>{{ statusLabel(bucket.label) }}</dt>
                <dd>{{ bucket.value }}</dd>
              </div>
              <div v-if="datasetStatusBuckets.length === 0" class="no-buckets">暂无数据集状态</div>
            </dl>
          </section>
        </div>
      </section>

      <section class="dashboard-section model-section">
        <div class="section-heading">
          <h2>模型空间分析</h2>
          <button type="button" class="quick-link" @click="goDataPreparation">快速访问</button>
        </div>
        <div class="dashboard-grid dashboard-grid-two">
          <PipelineStatusChart :buckets="pipelineStatusBuckets" />
          <CreationTrendChart :trend="pipelineTrend" />
        </div>
      </section>

      <section class="dashboard-section service-section">
        <div class="section-heading">
          <h2>服务列表分析</h2>
          <button type="button" class="quick-link" @click="goServices">快速访问</button>
        </div>
        <ServiceHealthPanel
          :health-buckets="resourceStatistics?.services.health_buckets ?? []"
          :calls="resourceStatistics?.services.calls ?? 0"
          :instances="resourceStatistics?.services.instances ?? 0"
        />
      </section>

      <p v-if="resourceError" class="resource-error" role="alert">
        {{ resourceError }}。正在展示最近一次成功获取的资源统计。
      </p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import {
  api,
  type ResourceStatistics,
  type StatisticsBucket,
  type StatisticsTrend,
  type WorkbenchStatistics,
} from "@/api/client";
import AsyncState from "@/components/common/AsyncState.vue";
import CreationTrendChart from "@/components/dashboard/CreationTrendChart.vue";
import DatasetTrendChart from "@/components/dashboard/DatasetTrendChart.vue";
import PipelineStatusChart from "@/components/dashboard/PipelineStatusChart.vue";
import ResourceUsagePanel, { type ResourceUsageValue } from "@/components/dashboard/ResourceUsagePanel.vue";
import ServiceHealthPanel from "@/components/dashboard/ServiceHealthPanel.vue";
import StatisticSummaryStrip, { type StatisticSummaryItem } from "@/components/dashboard/StatisticSummaryStrip.vue";

const RESOURCE_POLL_INTERVAL_MS = 5_000;

const router = useRouter();
const overview = ref<WorkbenchStatistics | null>(null);
const resourceStatistics = ref<ResourceStatistics | null>(null);
const overviewLoading = ref(true);
const resourceLoading = ref(false);
const overviewError = ref("");
const overviewErrorState = ref<"denied" | "error">("error");
const resourceError = ref("");
let resourcePollTimer: ReturnType<typeof setInterval> | null = null;

const isEmpty = computed(() => {
  if (!overview.value) return false;
  const { pipelines, datasets, training_jobs: trainingJobs, services, nodes } = overview.value.totals;
  return pipelines + datasets + trainingJobs + services + nodes === 0;
});

const summaryItems = computed<StatisticSummaryItem[]>(() => {
  if (!overview.value) return [];
  const totals = overview.value.totals;
  return [
    { label: "产线数量", value: totals.pipelines, unit: "个" },
    { label: "数据集数量", value: totals.datasets, unit: "个" },
    { label: "训练任务", value: totals.training_jobs, unit: "个" },
    { label: "服务数量", value: totals.services, unit: "个" },
    { label: "可用节点", value: totals.nodes, unit: "个" },
  ];
});

const pipelineStatusBuckets = computed(() => overview.value?.status_buckets.pipelines ?? []);
const datasetStatusBuckets = computed(() => overview.value?.status_buckets.datasets ?? []);
const pipelineTrend = computed<StatisticsTrend>(() => overview.value?.creation_trends.pipelines ?? emptyTrend());
const datasetTrend = computed<StatisticsTrend>(() => overview.value?.creation_trends.datasets ?? emptyTrend());

const resourceUsage = computed<ResourceUsageValue[]>(() => {
  const usage = resourceStatistics.value?.nodes.resource_usage;
  return [
    resourceValue("CPU", usage?.cpu_utilization_percent),
    resourceValue("内存", usage?.memory_utilization_percent),
    resourceValue("磁盘", usage?.disk_utilization_percent),
  ];
});

const gpuUsage = computed<ResourceUsageValue[]>(() =>
  (resourceStatistics.value?.gpus.series ?? []).map((gpu, index) => ({
    label: `GPU ${index + 1}`,
    value: gpu.utilization_percent.value,
    unit: "%",
    available: gpu.utilization_percent.available,
    unavailable: !gpu.utilization_percent.available,
  })),
);

const resourceStale = computed(() => {
  const freshness = resourceStatistics.value?.nodes.freshness;
  return Boolean(freshness && (freshness.stale > 0 || freshness.unknown > 0));
});

function emptyTrend(): StatisticsTrend {
  return { labels: [], values: [] };
}

function resourceValue(label: string, metric?: { value: number | null; available: number; unavailable: number }): ResourceUsageValue {
  return {
    label,
    value: metric?.value ?? null,
    unit: "%",
    available: Boolean(metric && metric.available > 0),
    unavailable: !metric || metric.unavailable > 0,
  };
}

async function loadOverview() {
  overviewLoading.value = true;
  overviewError.value = "";
  try {
    overview.value = await api.getWorkbenchStatistics();
  } catch (error) {
    overviewErrorState.value = errorStatus(error) === 403 ? "denied" : "error";
    overviewError.value = errorMessage(error, "工作台统计加载失败");
  } finally {
    overviewLoading.value = false;
  }
}

async function refreshResourceStatistics() {
  if (document.hidden || resourceLoading.value) return;
  resourceLoading.value = true;
  resourceError.value = "";
  try {
    resourceStatistics.value = await api.getResourceStatistics();
  } catch (error) {
    resourceError.value = errorMessage(error, "资源统计刷新失败");
  } finally {
    resourceLoading.value = false;
  }
}

function handleVisibilityChange() {
  if (document.hidden) {
    stopResourcePolling();
    return;
  }
  void refreshResourceStatistics();
  startResourcePolling();
}

function startResourcePolling() {
  if (document.hidden || resourcePollTimer !== null) return;
  resourcePollTimer = setInterval(() => {
    void refreshResourceStatistics();
  }, RESOURCE_POLL_INTERVAL_MS);
}

function stopResourcePolling() {
  if (resourcePollTimer === null) return;
  clearInterval(resourcePollTimer);
  resourcePollTimer = null;
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function errorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== "object" || !("status" in error)) return undefined;
  return typeof error.status === "number" ? error.status : undefined;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    created: "已创建",
    validated: "已校验",
    processing: "处理中",
    failed: "失败",
    running: "运行中",
    success: "成功",
    stopped: "已停止",
    draft: "配置中",
  };
  return labels[status] ?? status;
}

function formatTimestamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function goDataPreparation() {
  void router.push("/data-preparation");
}

function goServices() {
  void router.push("/services");
}

onMounted(() => {
  void loadOverview();
  void refreshResourceStatistics();
  document.addEventListener("visibilitychange", handleVisibilityChange);
  startResourcePolling();
});

onBeforeUnmount(() => {
  stopResourcePolling();
  document.removeEventListener("visibilitychange", handleVisibilityChange);
});
</script>

<style scoped>
.workbench-view {
  container: workbench / inline-size;
  display: grid;
  gap: 18px;
  min-width: 0;
  min-height: 100%;
  color: #18263b;
}

.workbench-header,
.section-heading {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 16px;
}

.workbench-header h1,
.section-heading h2 {
  margin: 0;
  color: #172033;
}

.workbench-header h1 { font-size: 24px; line-height: 32px; }
.workbench-header p,
.generated-at { margin: 4px 0 0; color: #718096; font-size: 13px; line-height: 20px; }
.generated-at { margin-top: 8px; white-space: nowrap; }

.quick-link {
  position: relative;
  min-width: 44px;
  min-height: 24px;
  padding: 0 4px;
  border: 0;
  background: transparent;
  color: #1763ff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
}

.quick-link::before {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 44px;
  height: 44px;
  content: "";
  transform: translate(-50%, -50%);
}

.dashboard-section {
  display: grid;
  gap: 12px;
  min-width: 0;
  padding: 18px;
  border: 1px solid #e7edf6;
  border-radius: 6px;
  background: #fff;
}

.section-heading h2 { font-size: 17px; line-height: 24px; }
.dashboard-grid { display: grid; gap: 18px; min-width: 0; }
.dashboard-grid-three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.dashboard-grid-two { grid-template-columns: repeat(2, minmax(0, 1fr)); }

.dataset-summary { min-width: 0; }
.dataset-summary h3 { margin: 0; color: #18263b; font-size: 15px; line-height: 22px; }
.dataset-summary dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 16px 0 0; }
.dataset-summary dl > div { min-width: 0; padding: 12px; border: 1px solid #edf1f7; border-radius: 4px; background: #fafcff; }
.dataset-summary dt { overflow: hidden; color: #718096; font-size: 12px; line-height: 18px; text-overflow: ellipsis; white-space: nowrap; }
.dataset-summary dd { margin: 5px 0 0; color: #26364e; font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
.dataset-summary .no-buckets { grid-column: 1 / -1; color: #8b98aa; text-align: center; }

.resource-error { margin: -4px 0 0; color: #a66c00; font-size: 12px; line-height: 18px; }

@container workbench (max-width: 1080px) {
  .dashboard-grid-three { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dashboard-grid-three > :last-child { grid-column: 1 / -1; }
}

@container workbench (max-width: 720px) {
  .workbench-header { align-items: start; flex-direction: column; gap: 0; }
  .generated-at { margin-top: 2px; white-space: normal; }
  .dashboard-grid-three,
  .dashboard-grid-two { grid-template-columns: minmax(0, 1fr); }
  .dashboard-grid-three > :last-child { grid-column: auto; }
}

@container workbench (max-width: 460px) {
  .dashboard-section { padding: 14px; }
  .dataset-summary dl { grid-template-columns: minmax(0, 1fr); }
}
</style>
