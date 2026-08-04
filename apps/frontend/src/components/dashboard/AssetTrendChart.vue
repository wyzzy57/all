<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { BarChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { DashboardTrend } from "./CreationTrendChart.vue";

const props = defineProps<{
  pipelineTrend: DashboardTrend;
  datasetTrend: DashboardTrend;
}>();

use([CanvasRenderer, BarChart, GridComponent, LegendComponent, TooltipComponent, AriaComponent]);

const chartElement = ref<HTMLElement | null>(null);
const axisLabels = computed(() => [
  ...new Set([...props.pipelineTrend.labels, ...props.datasetTrend.labels]),
]);
const hasData = computed(() => axisLabels.value.length > 0);

function alignTrend(trend: DashboardTrend, labels: string[]) {
  const values = new Map(trend.labels.map((label, index) => [label, trend.values[index] ?? 0]));
  return labels.map((label) => values.get(label) ?? 0);
}

const alignedRows = computed(() => {
  const labels = axisLabels.value;
  const pipelineValues = alignTrend(props.pipelineTrend, labels);
  const datasetValues = alignTrend(props.datasetTrend, labels);
  return labels.map((label, index) => ({
    label,
    pipeline: pipelineValues[index],
    dataset: datasetValues[index],
  }));
});

let chart: ReturnType<typeof init> | null = null;
let resizeObserver: ResizeObserver | null = null;
let hasAnimated = false;
const reducedMotionQuery = "(prefers-reduced-motion: reduce)";

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]!);
}

function formatTooltip(params: {
  axisValue?: string | number;
  marker?: string;
  seriesName?: string;
  value?: string | number;
} | Array<{
  axisValue?: string | number;
  marker?: string;
  seriesName?: string;
  value?: string | number;
}>) {
  const entries = Array.isArray(params) ? params : [params];
  const axisValue = entries[0]?.axisValue;
  const lines = entries.map((entry) => (
    `${entry.marker ?? ""}${escapeHtml(entry.seriesName ?? "")}: ${Number(entry.value ?? 0).toLocaleString("zh-CN")}`
  ));
  return [axisValue === undefined ? "" : escapeHtml(String(axisValue)), ...lines]
    .filter(Boolean)
    .join("<br>");
}

function option(animation: boolean) {
  const labels = axisLabels.value;
  const prefersReducedMotion = typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia(reducedMotionQuery).matches;
  return {
    animation: animation && !prefersReducedMotion,
    animationDuration: prefersReducedMotion ? 0 : 360,
    aria: { enabled: true },
    color: ["#4f78a8", "#6e9983"],
    grid: { top: 46, left: 12, right: 12, bottom: 28, containLabel: true },
    legend: {
      top: 0,
      data: ["产线", "数据集"],
      textStyle: { color: "#5f6873", fontSize: 12 },
      itemWidth: 12,
      itemHeight: 8,
    },
    tooltip: {
      trigger: "axis",
      formatter: formatTooltip,
    },
    xAxis: {
      type: "category",
      data: labels,
      axisTick: { alignWithLabel: true },
      axisLine: { lineStyle: { color: "#d9dde2" } },
      axisLabel: { color: "#5f6873", fontSize: 12, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      axisLabel: { color: "#5f6873", fontSize: 12 },
      splitLine: { lineStyle: { color: "#e5e7ea" } },
    },
    series: [
      {
        name: "产线",
        type: "bar",
        data: alignTrend(props.pipelineTrend, labels),
        barMaxWidth: 24,
        itemStyle: { borderRadius: [3, 3, 0, 0] },
      },
      {
        name: "数据集",
        type: "bar",
        data: alignTrend(props.datasetTrend, labels),
        barMaxWidth: 24,
        itemStyle: { borderRadius: [3, 3, 0, 0] },
      },
    ],
  };
}

function disposeChart() {
  resizeObserver?.disconnect();
  resizeObserver = null;
  chart?.dispose();
  chart = null;
}

function renderChart() {
  if (!hasData.value) {
    disposeChart();
    return;
  }
  if (!chart && chartElement.value) {
    chart = init(chartElement.value);
    chart.setOption(option(!hasAnimated), true);
    hasAnimated = true;
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(() => chart?.resize());
      resizeObserver.observe(chartElement.value);
    }
    return;
  }
  chart?.setOption(option(false), true);
}

watch(
  () => [props.pipelineTrend, props.datasetTrend],
  renderChart,
  { deep: true, flush: "post" },
);
onMounted(renderChart);
onBeforeUnmount(disposeChart);
</script>

<template>
  <section class="asset-trend-chart" aria-label="资产趋势图">
    <div
      v-if="!hasData"
      data-testid="asset-trend-empty"
      class="asset-trend-empty"
      :style="{ minHeight: '168px' }"
    >
      暂无资产趋势数据
    </div>
    <div
      v-else
      ref="chartElement"
      class="dashboard-chart asset-trend-canvas"
      :style="{ minHeight: '168px' }"
      tabindex="0"
      role="img"
      aria-label="资产趋势图，按时间展示产线与数据集数量"
    />
    <ul v-if="hasData" data-testid="asset-trend-a11y" class="sr-only" aria-label="资产趋势数据">
      <li v-for="row in alignedRows" :key="row.label">
        {{ row.label }}，产线：{{ row.pipeline }}，数据集：{{ row.dataset }}
      </li>
    </ul>
  </section>
</template>

<style scoped>
.asset-trend-chart {
  min-width: 0;
}

.dashboard-chart {
  width: 100%;
  aspect-ratio: 16 / 7;
}

.dashboard-chart:focus-visible {
  outline: 2px solid #3568a8;
  outline-offset: 2px;
}

.asset-trend-empty {
  display: grid;
  place-items: center;
  padding: 20px;
  color: #5f6873;
  background: #f5f6f7;
  font-size: 13px;
  line-height: 20px;
  text-align: center;
  overflow-wrap: anywhere;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 520px) {
  .dashboard-chart {
    aspect-ratio: 4 / 3;
  }
}
</style>
