<template>
  <section class="workbench-view">
    <header class="workbench-header">
      <div>
        <h1>工作台</h1>
        <p>训练、数据与服务运行概览</p>
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

      <div class="command-grid">
        <div class="command-primary">
          <section class="command-panel trend-panel">
            <DashboardPanelHeading title="资产增长趋势" meta="近 6 个月" />
            <AssetTrendChart
              :pipeline-trend="pipelineTrend"
              :dataset-trend="datasetTrend"
            />
          </section>

          <section class="command-panel pipeline-status-panel">
            <DashboardPanelHeading
              title="产线运行状态"
              :meta="`共 ${overview.totals.pipelines} 条`"
            />
            <StatusSummaryRow :buckets="pipelineStatusBuckets" />
          </section>
        </div>

        <section class="command-panel resource-panel">
          <ResourceUsagePanel
            :usage="resourceUsage"
            :gpu-series="gpuUsage"
            :stale="resourceStale"
          />
        </section>
      </div>

      <div class="overview-grid">
        <section class="command-panel dataset-panel">
          <DashboardPanelHeading
            title="数据集状态"
            action-label="查看数据资产"
            data-testid="go-data-assets"
            @action="goDataPreparation"
          />
          <PipelineStatusChart :buckets="datasetStatusBuckets" title="数据集状态" status-context="dataset" />
        </section>

        <section class="command-panel service-panel">
          <DashboardPanelHeading
            title="服务健康"
            action-label="查看服务"
            data-testid="go-services"
            @action="goServices"
          />
          <ServiceHealthPanel
            :health-buckets="serviceHealthBuckets"
            :calls="serviceCalls"
            :instances="serviceInstances"
          />
        </section>

        <section class="command-panel activity-panel">
          <DashboardPanelHeading
            title="当前活动"
            action-label="查看模型空间"
            data-testid="go-model-space"
            @action="goModelSpace"
          />
          <ActivitySummaryPanel v-bind="activity" />
        </section>
      </div>

      <p v-if="resourceErrorMessage" class="resource-error" role="alert">
        {{ resourceErrorMessage }}
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
import ActivitySummaryPanel from "@/components/dashboard/ActivitySummaryPanel.vue";
import AssetTrendChart from "@/components/dashboard/AssetTrendChart.vue";
import DashboardPanelHeading from "@/components/dashboard/DashboardPanelHeading.vue";
import PipelineStatusChart from "@/components/dashboard/PipelineStatusChart.vue";
import ResourceUsagePanel, { type ResourceUsageValue } from "@/components/dashboard/ResourceUsagePanel.vue";
import ServiceHealthPanel from "@/components/dashboard/ServiceHealthPanel.vue";
import StatisticSummaryStrip, { type StatisticSummaryItem } from "@/components/dashboard/StatisticSummaryStrip.vue";
import StatusSummaryRow from "@/components/dashboard/StatusSummaryRow.vue";

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
    { label: "可用节点", value: resourceStatistics.value?.nodes.freshness.fresh ?? null, unit: "个" },
  ];
});

const pipelineStatusBuckets = computed(() => overview.value?.status_buckets.pipelines ?? []);
const datasetStatusBuckets = computed(() => overview.value?.status_buckets.datasets ?? []);
const pipelineTrend = computed<StatisticsTrend>(() => overview.value?.creation_trends.pipelines ?? emptyTrend());
const datasetTrend = computed<StatisticsTrend>(() => overview.value?.creation_trends.datasets ?? emptyTrend());
const serviceHealthBuckets = computed(() => resourceStatistics.value?.services.health_buckets ?? []);
const serviceCalls = computed(() => resourceStatistics.value?.services.calls ?? 0);
const serviceInstances = computed(() => resourceStatistics.value?.services.instances ?? 0);

const activity = computed(() => ({
  training: sumBuckets(
    overview.value?.status_buckets.training_jobs ?? [],
    new Set(["running", "training"]),
  ),
  deployments: sumBuckets(
    overview.value?.status_buckets.services ?? [],
    new Set(["deploying", "starting"]),
  ),
  anomalies: (resourceStatistics.value?.nodes.freshness.stale ?? 0)
    + (resourceStatistics.value?.nodes.freshness.unknown ?? 0)
    + sumBuckets(
      resourceStatistics.value?.services.health_buckets ?? [],
      new Set(["unhealthy", "failed", "error", "degraded"]),
    ),
}));

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

const resourceErrorMessage = computed(() => {
  if (!resourceError.value) return "";
  return resourceStatistics.value
    ? `${resourceError.value}。正在展示最近一次成功获取的资源统计。`
    : `${resourceError.value}。资源统计暂时不可用，正在重试。`;
});

function emptyTrend(): StatisticsTrend {
  return { labels: [], values: [] };
}

function sumBuckets(buckets: StatisticsBucket[], labels: Set<string>) {
  return buckets.reduce((sum, bucket) => (
    labels.has(bucket.label.trim().toLowerCase()) ? sum + bucket.value : sum
  ), 0);
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
  try {
    resourceStatistics.value = await api.getResourceStatistics();
    resourceError.value = "";
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

function goModelSpace() {
  void router.push("/model-space");
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
  --workbench-surface: #f3f4f6;
  --workbench-surface-raised: #f6f7f8;
  --workbench-border: #e0e2e6;

  container: workbench / inline-size;
  display: grid;
  grid-template-rows: auto 58px minmax(0, 310px) minmax(0, 240px) auto;
  gap: 10px;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 100%;
  color: #252a31;
  overflow-x: clip;
}

.workbench-header {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
  padding: 2px 2px 0;
}

.workbench-header > div {
  min-width: 0;
}

.workbench-header h1 {
  margin: 0;
  color: #252a31;
  font-size: 24px;
  line-height: 32px;
  overflow-wrap: anywhere;
}

.workbench-header p,
.generated-at {
  margin: 4px 0 0;
  color: #5f6873;
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.generated-at {
  margin-top: 8px;
  text-align: right;
  white-space: nowrap;
}

.command-grid,
.overview-grid,
.command-primary {
  display: grid;
  min-width: 0;
}

.command-grid {
  grid-template-columns: minmax(0, 1.7fr) minmax(280px, .8fr);
  gap: 10px;
  align-items: stretch;
}

.command-primary {
  grid-template-rows: minmax(0, 208px) minmax(0, 94px);
  gap: 8px;
}

.overview-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  align-items: stretch;
}

.command-panel,
.statistic-summary-strip {
  min-width: 0;
  max-width: 100%;
  border: 1px solid var(--workbench-border);
  border-radius: 8px;
  background: var(--workbench-surface);
}

.command-panel {
  display: grid;
  align-content: start;
  gap: 8px;
  padding: 12px;
}

.trend-panel :deep(.asset-trend-chart),
.pipeline-status-panel :deep(.status-summary-row),
.resource-panel :deep(.resource-usage-panel),
.dataset-panel :deep(.pipeline-status-chart),
.service-panel :deep(.service-health-panel),
.activity-panel :deep(.activity-summary-panel) {
  background: transparent;
}

.dataset-panel :deep(.pipeline-status-chart > header > h3),
.service-panel :deep(.service-health-panel > header > h3) {
  display: none;
}

.workbench-view :deep(.statistic-summary-strip),
.workbench-view :deep(.statistic-summary-grid),
.workbench-view :deep(.summary-empty) {
  min-height: 58px;
}

.workbench-view :deep(.summary-item) {
  gap: 2px;
  padding: 4px 14px;
}

.trend-panel :deep(.asset-trend-canvas),
.trend-panel :deep(.asset-trend-empty) {
  box-sizing: border-box;
  height: 168px;
  aspect-ratio: auto !important;
}

.trend-panel :deep(.asset-trend-canvas) {
  overflow: hidden;
}

.pipeline-status-panel :deep(.status-summary-item),
.pipeline-status-panel :deep(.status-summary-empty) {
  box-sizing: border-box;
  min-height: 52px;
  padding: 6px 12px;
}

.dataset-panel :deep(.pipeline-status-chart .dashboard-chart),
.dataset-panel :deep(.pipeline-status-chart .dashboard-empty) {
  box-sizing: border-box;
  height: 168px;
  min-height: 168px !important;
  aspect-ratio: auto !important;
}

.dataset-panel :deep(.pipeline-status-chart .dashboard-chart) {
  overflow: hidden;
}

.service-panel :deep(.service-health-panel) {
  gap: 4px;
}

.service-panel :deep(.service-health-panel .dashboard-chart),
.service-panel :deep(.service-health-panel .dashboard-empty) {
  box-sizing: border-box;
  height: 128px;
  min-height: 128px !important;
  aspect-ratio: auto !important;
}

.service-panel :deep(.service-health-panel .dashboard-chart) {
  overflow: hidden;
}

.service-panel :deep(.health-summary li) {
  padding-block: 3px;
}

.resource-panel :deep(.resource-usage-panel) {
  align-content: stretch;
  gap: 8px;
  height: 100%;
  min-height: 0 !important;
}

.resource-panel :deep(.resource-grid) {
  align-content: space-between;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.resource-panel :deep(.resource-row) {
  gap: 5px;
}

.activity-panel :deep(.activity-summary-item) {
  padding-block: 8px;
}

.trend-panel {
  gap: 4px;
  min-height: 0;
  padding: 10px;
  background: var(--workbench-surface-raised);
}

.pipeline-status-panel {
  gap: 4px;
  min-height: 0;
  padding: 8px;
}

.resource-panel {
  align-content: stretch;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.dataset-panel,
.service-panel,
.activity-panel {
  gap: 6px;
  min-height: 0;
  padding: 10px;
}

.resource-error {
  margin: -2px 2px 0;
  color: #8a641f;
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}

@container workbench (max-width: 1060px) {
  .workbench-view {
    grid-template-rows: none;
  }

  .command-grid,
  .command-primary,
  .overview-grid {
    grid-template-rows: none;
    min-height: 0;
  }

  .command-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .overview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .activity-panel {
    grid-column: 1 / -1;
    min-height: 0;
  }

  .resource-panel {
    min-height: 0;
    overflow-y: auto;
  }

  .resource-panel :deep(.resource-grid) {
    grid-template-columns: minmax(0, 1fr);
  }

  .trend-panel :deep(.asset-trend-canvas),
  .trend-panel :deep(.asset-trend-empty) {
    height: auto;
  }

  .trend-panel :deep(.asset-trend-canvas) {
    aspect-ratio: 16 / 7 !important;
  }
}

@container workbench (max-width: 720px) {
  .workbench-header {
    flex-direction: column;
    gap: 0;
  }

  .generated-at {
    margin-top: 2px;
    text-align: left;
    white-space: normal;
  }

  .overview-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .activity-panel {
    grid-column: auto;
  }

  .command-panel {
    min-height: 0;
  }
}

@media (max-width: 460px) {
  .workbench-view {
    gap: 12px;
    font-size: 12px;
  }
}

@container workbench (max-width: 460px) {
  .workbench-header {
    padding-inline: 0;
  }

  .command-grid,
  .command-primary,
  .overview-grid {
    gap: 12px;
  }

  .command-panel {
    padding: 12px;
  }
}
</style>
