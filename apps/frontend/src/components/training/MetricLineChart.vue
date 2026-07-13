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

const props = defineProps<{
  series: TrainingObservabilityScalars["series"];
  unit?: string;
  height?: string | number;
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

function seriesOption() {
  return Object.entries(props.series).map(([name, points]) => ({
    name,
    type: "line",
    showSymbol: false,
    connectNulls: false,
    data: points.map((point) => [point.step, point.value])
  }));
}

function initialOption() {
  return {
    animation: false,
    grid: { left: 52, right: 28, top: 42, bottom: 58, containLabel: true },
    legend: { type: "scroll", top: 8 },
    tooltip: { trigger: "axis" },
    toolbox: { right: 8, feature: { saveAsImage: {} } },
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 8 }],
    xAxis: { type: "value", name: "Step", min: "dataMin" },
    yAxis: { type: "value", name: props.unit },
    series: seriesOption()
  };
}

function updateChart() {
  chart?.setOption(
    {
      yAxis: { name: props.unit },
      series: seriesOption()
    },
    { replaceMerge: ["series"] }
  );
}

function resizeChart() {
  chart?.resize();
}

watch(() => [props.series, props.unit], updateChart, { deep: true });

onMounted(() => {
  chart = init(chartElement.value!);
  chart.setOption(initialOption(), true);
  window.addEventListener("resize", resizeChart);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", resizeChart);
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
