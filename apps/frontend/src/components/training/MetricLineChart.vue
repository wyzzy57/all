<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { LineChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  ToolboxComponent,
  TooltipComponent
} from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { TrainingObservabilityScalars } from "@/api/client";
import { smoothMetricPoints, type MetricValueFormat } from "./trainingMetricCatalog";

const props = defineProps<{
  series: TrainingObservabilityScalars["series"];
  unit?: string;
  height?: string | number;
  axisMin?: number;
  axisMax?: number;
  valueFormat?: MetricValueFormat;
  smoothing?: number;
}>();

use([
  CanvasRenderer,
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  ToolboxComponent
]);

const chartElement = ref<HTMLElement | null>(null);
const chartHeight = computed(() => (typeof props.height === "number" ? `${props.height}px` : (props.height ?? "320px")));
let chart: ReturnType<typeof init> | null = null;
let resizeObserver: ResizeObserver | null = null;

type TooltipPoint = {
  marker?: string;
  seriesName?: string;
  data?: { rawValue?: number };
  value?: [number, number];
};

function formatValue(value: number) {
  if (!Number.isFinite(value)) return "-";
  if (props.valueFormat === "scientific") return value.toExponential(3);
  if (props.valueFormat === "percent") return `${value.toFixed(2)}%`;
  if (props.valueFormat === "ratio") return value.toFixed(4);
  if (Math.abs(value) >= 1000) return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return value.toFixed(4).replace(/\.?0+$/, "");
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]!);
}

function tooltipFormatter(params: TooltipPoint | TooltipPoint[]) {
  const items = Array.isArray(params) ? params : [params];
  const step = items[0]?.value?.[0];
  const rows = items.map((item) => {
    const rawValue = item.data?.rawValue ?? item.value?.[1];
    return `${item.marker ?? ""}${escapeHtml(item.seriesName ?? "")}: ${rawValue === undefined ? "-" : formatValue(rawValue)}`;
  });
  return [`Step ${step ?? "-"}`, ...rows].join("<br />");
}

function seriesOption() {
  return Object.entries(props.series).map(([name, points]) => ({
    name,
    type: "line",
    showSymbol: false,
    connectNulls: false,
    data: smoothMetricPoints(points, props.smoothing ?? 0).map((point) => ({
      value: [point.step, point.displayValue],
      rawValue: point.value,
    }))
  }));
}

function yAxisOption() {
  return {
    type: "value",
    name: props.unit,
    min: props.axisMin,
    max: props.axisMax,
    axisLabel: { formatter: (value: number) => formatValue(value) },
  };
}

function initialOption() {
  return {
    animation: false,
    grid: { left: 52, right: 28, top: 42, bottom: 58, containLabel: true },
    legend: { type: "scroll", top: 8 },
    tooltip: { trigger: "axis", formatter: tooltipFormatter },
    toolbox: { right: 8, feature: { saveAsImage: {} } },
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 8 }],
    xAxis: { type: "value", name: "Step", min: "dataMin" },
    yAxis: yAxisOption(),
    series: seriesOption()
  };
}

function updateChart() {
  chart?.setOption(
    {
      yAxis: yAxisOption(),
      series: seriesOption()
    },
    { replaceMerge: ["series"] }
  );
}

function resizeChart() {
  chart?.resize();
}

watch(
  () => [props.series, props.unit, props.axisMin, props.axisMax, props.valueFormat, props.smoothing],
  updateChart,
  { deep: true },
);

onMounted(() => {
  chart = init(chartElement.value!);
  chart.setOption(initialOption(), true);
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(resizeChart);
    resizeObserver.observe(chartElement.value!);
  }
  window.addEventListener("resize", resizeChart);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", resizeChart);
  resizeObserver?.disconnect();
  resizeObserver = null;
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div ref="chartElement" class="training-chart" :style="{ height: chartHeight }" />
</template>

<style scoped>
.training-chart {
  width: 100%;
  min-height: 240px;
}
</style>
