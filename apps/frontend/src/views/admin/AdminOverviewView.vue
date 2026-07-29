<template>
  <section class="admin-overview">
    <header class="page-heading">
      <div><h1>平台总览</h1><p>组织资源、运行状态与近期异常的实时概览</p></div>
      <time v-if="overview">统计生成于 {{ formatTime(overview.generated_at) }}</time>
    </header>

    <AsyncState
      v-if="loading"
      state="loading"
      title="正在加载平台统计..."
      test-id="admin-overview-loading"
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
      title="暂无平台资源"
      test-id="admin-overview-empty"
    />

    <template v-else-if="overview">
      <StatisticSummaryStrip :items="summaryItems" />

      <div class="chart-grid">
        <section class="overview-section">
          <div class="section-heading"><button data-testid="go-model-space" @click="go('/model-space')">产线状态</button></div>
          <PipelineStatusChart :buckets="overview.status_buckets.pipelines ?? []" title="产线状态分析" />
        </section>
        <section class="overview-section">
          <div class="section-heading"><button @click="go('/model-space')">产线创建趋势</button></div>
          <CreationTrendChart :trend="overview.creation_trends.pipelines ?? emptyTrend" title="产线创建时间分析" />
        </section>
        <section class="overview-section">
          <div class="section-heading"><button data-testid="go-data-preparation" @click="go('/data-preparation')">数据集趋势</button></div>
          <DatasetTrendChart :trend="overview.creation_trends.datasets ?? emptyTrend" title="数据集分析" />
        </section>
      </div>

      <div class="operations-grid">
        <section class="overview-section">
          <div class="section-heading"><button data-testid="go-resources" @click="go('/admin/resources')">节点与资源</button></div>
          <ResourceUsagePanel :usage="resourceUsage" :gpu-series="gpuUsage" :stale="resourceStale" />
        </section>
        <section class="overview-section">
          <div class="section-heading"><button data-testid="go-services" @click="go('/services')">服务健康</button></div>
          <ServiceHealthPanel
            :health-buckets="resources?.services.health_buckets ?? []"
            :calls="resources?.services.calls ?? 0"
            :instances="resources?.services.instances ?? 0"
          />
        </section>
      </div>

      <section class="overview-section allocation-section" data-testid="group-allocation">
        <div class="section-heading"><button @click="go('/admin/authorization')">分组资源分配</button></div>
        <dl class="allocation-grid">
          <div><dt>分配策略</dt><dd>{{ allocation.policy_count }}</dd></div>
          <div><dt>资源池</dt><dd>{{ allocation.resource_pool_count }}</dd></div>
          <div><dt>训练任务</dt><dd>{{ allocation.active_training_runs }}</dd></div>
          <div><dt>服务实例</dt><dd>{{ allocation.active_service_instances }}</dd></div>
          <div><dt>活跃负载</dt><dd>{{ allocation.active_workloads }}</dd></div>
        </dl>
        <p v-if="allocation.limitation" class="limitation">{{ allocation.limitation }}</p>
      </section>

      <section class="overview-section failures-section" data-testid="recent-failures">
        <div class="section-heading"><h2>近期失败</h2></div>
        <div class="failure-table-wrap">
          <table>
            <thead><tr><th>资源</th><th>类型</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="failure in overview.recent_failures" :key="`${failure.resource_type}-${failure.resource_id}`">
                <td>{{ failure.name }}</td><td>{{ resourceTypeLabel(failure.resource_type) }}</td>
                <td><span class="failure-status">{{ failure.status }}</span></td><td>{{ formatTime(failure.updated_at) }}</td>
                <td><button :data-testid="`failure-${failure.resource_type}-${failure.resource_id}`" @click="openFailure(failure)">查看</button></td>
              </tr>
              <tr v-if="overview.recent_failures.length === 0"><td colspan="5" class="empty-row">暂无近期失败记录</td></tr>
            </tbody>
          </table>
        </div>
      </section>
      <p v-if="resourceError" class="resource-error" role="alert">{{ resourceError }}。正在展示最近一次成功获取的资源统计。</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api, type AdminStatistics, type RecentFailure, type ResourceStatistics, type StatisticsTrend } from "@/api/client";
import AsyncState from "@/components/common/AsyncState.vue";
import CreationTrendChart from "@/components/dashboard/CreationTrendChart.vue";
import DatasetTrendChart from "@/components/dashboard/DatasetTrendChart.vue";
import PipelineStatusChart from "@/components/dashboard/PipelineStatusChart.vue";
import ResourceUsagePanel, { type ResourceUsageValue } from "@/components/dashboard/ResourceUsagePanel.vue";
import ServiceHealthPanel from "@/components/dashboard/ServiceHealthPanel.vue";
import StatisticSummaryStrip, { type StatisticSummaryItem } from "@/components/dashboard/StatisticSummaryStrip.vue";

const POLL_INTERVAL = 5_000;
const router = useRouter();
const overview = ref<AdminStatistics | null>(null);
const resources = ref<ResourceStatistics | null>(null);
const loading = ref(true);
const overviewError = ref("");
const overviewErrorState = ref<"denied" | "error">("error");
const resourceError = ref("");
const resourceLoading = ref(false);
let timer: ReturnType<typeof setInterval> | null = null;
const emptyTrend: StatisticsTrend = { labels: [], values: [] };
const emptyAllocation = { policy_count: 0, resource_pool_count: 0, active_training_runs: 0, active_service_instances: 0, active_workloads: 0, limitation: "" };

const isEmpty = computed(() => overview.value ? Object.values(overview.value.totals).every((value) => value === 0) : false);
const summaryItems = computed<StatisticSummaryItem[]>(() => overview.value ? [
  { label: "产线数量", value: overview.value.totals.pipelines, unit: "个" },
  { label: "数据集数量", value: overview.value.totals.datasets, unit: "个" },
  { label: "训练任务", value: overview.value.totals.training_jobs, unit: "个" },
  { label: "服务数量", value: overview.value.totals.services, unit: "个" },
  { label: "节点数量", value: overview.value.totals.nodes, unit: "个" },
  { label: "用户数量", value: overview.value.totals.users, unit: "个" },
  { label: "用户分组", value: overview.value.totals.groups, unit: "个" },
] : []);
const allocation = computed(() => resources.value?.group_allocation_usage ?? emptyAllocation);
const resourceStale = computed(() => Boolean(resources.value && (resources.value.nodes.freshness.stale > 0 || resources.value.nodes.freshness.unknown > 0)));
const resourceUsage = computed<ResourceUsageValue[]>(() => {
  const usage = resources.value?.nodes.resource_usage;
  return [metric("CPU", usage?.cpu_utilization_percent), metric("内存", usage?.memory_utilization_percent), metric("磁盘", usage?.disk_utilization_percent)];
});
const gpuUsage = computed<ResourceUsageValue[]>(() => (resources.value?.gpus.series ?? []).map((gpu, index) => ({
  label: `GPU ${index + 1}`, value: gpu.utilization_percent.value, unit: "%", available: gpu.utilization_percent.available,
})));

function metric(label: string, value?: { value: number | null; available: number; unavailable: number }): ResourceUsageValue {
  return { label, value: value?.value ?? null, unit: "%", available: Boolean(value && value.available > 0), unavailable: !value || value.unavailable > 0 };
}
async function loadOverview() {
  loading.value = true; overviewError.value = "";
  try { overview.value = await api.getAdminOverviewStatistics(); }
  catch (error) { overviewErrorState.value = errorStatus(error) === 403 ? "denied" : "error"; overviewError.value = message(error, "平台统计加载失败"); }
  finally { loading.value = false; }
}
async function refreshResources() {
  if (document.hidden || resourceLoading.value) return;
  resourceLoading.value = true; resourceError.value = "";
  try { resources.value = await api.getAdminResourceStatistics(); }
  catch (error) { resourceError.value = message(error, "资源统计刷新失败"); }
  finally { resourceLoading.value = false; }
}
function startPolling() { if (!document.hidden && timer === null) timer = setInterval(() => void refreshResources(), POLL_INTERVAL); }
function stopPolling() { if (timer !== null) clearInterval(timer); timer = null; }
function visibilityChanged() { if (document.hidden) return stopPolling(); void refreshResources(); startPolling(); }
function go(path: string) { void router.push(path); }
function openFailure(failure: RecentFailure) {
  const targets: Record<RecentFailure["resource_type"], string> = {
    pipeline: `/model-space?pipeline=${failure.resource_id}`,
    training_job: `/training-visualization?job=${failure.resource_id}`,
    service: `/services/${failure.resource_id}`,
    node: "/admin/resources",
  };
  go(targets[failure.resource_type]);
}
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false }); }
function resourceTypeLabel(type: RecentFailure["resource_type"]) { return ({ pipeline: "产线", training_job: "训练任务", service: "服务", node: "节点" })[type]; }
function message(error: unknown, fallback: string) { return error instanceof Error && error.message ? error.message : fallback; }
function errorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== "object" || !("status" in error)) return undefined;
  return typeof error.status === "number" ? error.status : undefined;
}

onMounted(() => { void loadOverview(); void refreshResources(); document.addEventListener("visibilitychange", visibilityChanged); startPolling(); });
onBeforeUnmount(() => { stopPolling(); document.removeEventListener("visibilitychange", visibilityChanged); });
</script>

<style scoped>
.admin-overview { container: admin-overview / inline-size; display: grid; gap: 16px; min-width: 0; color: #172033; }
.page-heading,.section-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
.page-heading h1,.section-heading h2 { margin: 0; }.page-heading h1 { font-size: 24px; line-height: 32px; }
.page-heading p,.page-heading time { margin: 4px 0 0; color: #718096; font-size: 13px; }.page-heading time { margin-top: 8px; }
.section-heading button,.failures-section button { position: relative; min-width: 44px; min-height: 28px; padding: 0 4px; border: 0; background: transparent; color: #1763ff; cursor: pointer; font: inherit; }
.section-heading button::before,
.failures-section button::before {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 44px;
  height: 44px;
  content: "";
  transform: translate(-50%, -50%);
}
.chart-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 14px; }.operations-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 14px; }
.overview-section { min-width: 0; padding: 16px; border: 1px solid #e7edf6; border-radius: 6px; background: #fff; }.section-heading { margin-bottom: 10px; }.section-heading button,.section-heading h2 { font-size: 15px; font-weight: 600; }
.allocation-grid { display: grid; grid-template-columns: repeat(5,minmax(0,1fr)); gap: 0; margin: 0; }.allocation-grid div { padding: 10px 14px; border-right: 1px solid #edf1f7; }.allocation-grid div:last-child { border: 0; }.allocation-grid dt { color: #718096; font-size: 12px; }.allocation-grid dd { margin: 5px 0 0; font-size: 22px; font-weight: 600; }.limitation { margin: 10px 14px 0; color: #8b98aa; font-size: 12px; }
.failure-table-wrap { overflow-x: auto; }table { width: 100%; border-collapse: collapse; font-size: 13px; }th,td { padding: 11px 12px; border-bottom: 1px solid #edf1f7; text-align: left; white-space: nowrap; }th { color: #66758c; font-weight: 500; }.failure-status { color: #b42318; }.empty-row { color: #8b98aa; text-align: center; }.resource-error { margin: 0; color: #a66c00; font-size: 12px; }
@container admin-overview (max-width: 1120px) { .chart-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }.chart-grid > :last-child { grid-column: 1/-1; } }
@container admin-overview (max-width: 760px) { .chart-grid,.operations-grid { grid-template-columns: minmax(0,1fr); }.chart-grid > :last-child { grid-column: auto; }.allocation-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }.page-heading { flex-direction: column; } }
</style>
