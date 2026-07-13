<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { BarChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, ToolboxComponent, TooltipComponent } from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { TrainingObservabilityHistogram } from "@/api/client";

const props = defineProps<{
  histogram: TrainingObservabilityHistogram;
  height?: string | number;
}>();

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent, DataZoomComponent, ToolboxComponent]);

const chartElement = ref<HTMLElement | null>(null);
const chartHeight = computed(() => (typeof props.height === "number" ? `${props.height}px` : (props.height ?? "320px")));
let chart: ReturnType<typeof init> | null = null;

function histogramData() {
  return props.histogram.buckets.map((bucket) => [
    (bucket.lower + bucket.upper) / 2,
    bucket.count,
    bucket.lower,
    bucket.upper
  ]);
}

function initialOption() {
  return {
    animation: false,
    grid: { left: 52, right: 28, top: 38, bottom: 58, containLabel: true },
    tooltip: { trigger: "axis" },
    toolbox: { right: 8, feature: { saveAsImage: {} } },
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 8 }],
    xAxis: { type: "value", name: "Value", min: "dataMin", max: "dataMax" },
    yAxis: { type: "value", name: "Count", min: 0 },
    series: [
      {
        name: props.histogram.tag,
        type: "bar",
        encode: { x: 0, y: 1 },
        data: histogramData()
      }
    ]
  };
}

function updateChart() {
  chart?.setOption({
    series: [{ name: props.histogram.tag, type: "bar", data: histogramData() }]
  });
}

function resizeChart() {
  chart?.resize();
}

watch(() => props.histogram, updateChart, { deep: true });

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
