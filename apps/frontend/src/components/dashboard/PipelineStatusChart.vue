<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { PieChart } from "echarts/charts";
import { AriaComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

export type DashboardBucket = { label: string; value: number };

const props = withDefaults(defineProps<{
  buckets: DashboardBucket[];
  title?: string;
}>(), { title: "产线状态" });

use([CanvasRenderer, PieChart, LegendComponent, TooltipComponent, AriaComponent]);

const chartElement = ref<HTMLElement | null>(null);
const hasData = computed(() => props.buckets.some((bucket) => bucket.value > 0));
let chart: ReturnType<typeof init> | null = null;
let resizeObserver: ResizeObserver | null = null;
let hasAnimated = false;

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]!);
}

function option(animation: boolean) {
  return {
    animation,
    animationDuration: 360,
    aria: { enabled: true },
    color: ["#2f73f7", "#5c92f6", "#a8c5fb", "#f5b638", "#ef7b7b", "#7b8aa4"],
    tooltip: {
      trigger: "item",
      formatter: (params: { name: string; value: number; marker?: string }) => `${params.marker ?? ""}${escapeHtml(params.name)}: ${Number(params.value).toLocaleString("zh-CN")}`,
    },
    legend: { type: "scroll", bottom: 0, data: props.buckets.map((bucket) => bucket.label), textStyle: { color: "#5c6b80", fontSize: 12 } },
    series: [{
      type: "pie",
      radius: ["45%", "70%"],
      center: ["50%", "44%"],
      avoidLabelOverlap: true,
      selectedMode: false,
      data: props.buckets.map((bucket) => ({ name: bucket.label, value: bucket.value })),
      label: { show: false },
      emphasis: { scale: true, scaleSize: 8, label: { show: false } },
    }],
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

watch(() => props.buckets, renderChart, { deep: true, flush: "post" });
onMounted(renderChart);
onBeforeUnmount(disposeChart);
</script>

<template>
  <section class="dashboard-panel pipeline-status-chart" :aria-label="title">
    <header><h3>{{ title }}</h3></header>
    <div v-if="!hasData" data-testid="pipeline-status-empty" class="dashboard-empty">暂无产线状态数据</div>
    <div v-else ref="chartElement" class="dashboard-chart" :style="{ aspectRatio: '16 / 10', minHeight: '240px' }" />
    <ul class="sr-only"><li v-for="bucket in buckets" :key="bucket.label">{{ bucket.label }}：{{ bucket.value }}</li></ul>
  </section>
</template>

<style scoped>
.dashboard-panel { min-width: 0; background: #fff; }
h3 { margin: 0; color: #18263b; font-size: 15px; line-height: 22px; }
.dashboard-chart { width: 100%; }
.dashboard-empty { display: grid; min-height: 240px; place-items: center; color: #8b98aa; font-size: 13px; border: 1px dashed #dbe4f1; border-radius: 6px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
