<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { PieChart } from "echarts/charts";
import { AriaComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { DashboardBucket } from "./PipelineStatusChart.vue";

const props = withDefaults(defineProps<{ healthBuckets: DashboardBucket[]; calls: number; instances: number; title?: string }>(), { title: "服务分析" });
use([CanvasRenderer, PieChart, LegendComponent, TooltipComponent, AriaComponent]);
const chartElement = ref<HTMLElement | null>(null);
const hasData = computed(() => props.healthBuckets.some((bucket) => bucket.value > 0));
const chartColors = ["#2563eb", "#16835b", "#b26a00", "#c2413a", "#6b7280"];
let chart: ReturnType<typeof init> | null = null;
let resizeObserver: ResizeObserver | null = null;
let hasAnimated = false;
function escapeHtml(value: string) { return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]!); }
function option(animation: boolean) { return { animation, animationDuration: 360, aria: { enabled: true }, color: chartColors, tooltip: { trigger: "item", formatter: (params: { name: string; value: number; marker?: string }) => `${params.marker ?? ""}${escapeHtml(params.name)}: ${Number(params.value).toLocaleString("zh-CN")}` }, legend: { type: "scroll", bottom: 0, data: props.healthBuckets.map((item) => item.label), textStyle: { color: "#4b5563", fontSize: 12 } }, series: [{ type: "pie", radius: ["42%", "66%"], center: ["50%", "45%"], selectedMode: false, data: props.healthBuckets.map((item) => ({ name: item.label, value: item.value })), label: { show: false }, emphasis: { scale: true, scaleSize: 8, label: { show: false } } }] }; }
function disposeChart() { resizeObserver?.disconnect(); resizeObserver = null; chart?.dispose(); chart = null; }
function renderChart() { if (!hasData.value) { disposeChart(); return; } if (!chart && chartElement.value) { chart = init(chartElement.value); chart.setOption(option(!hasAnimated), true); hasAnimated = true; if (typeof ResizeObserver !== "undefined") { resizeObserver = new ResizeObserver(() => chart?.resize()); resizeObserver.observe(chartElement.value); } return; } chart?.setOption(option(false), true); }
watch(() => props.healthBuckets, renderChart, { deep: true, flush: "post" }); onMounted(renderChart); onBeforeUnmount(disposeChart);
</script>

<template>
  <section class="service-health-panel" aria-label="服务健康状态">
    <header><h3>{{ title }}</h3><dl><div><dt>服务调用</dt><dd>{{ calls.toLocaleString("zh-CN") }}</dd></div><div><dt>运行实例</dt><dd>{{ instances.toLocaleString("zh-CN") }}</dd></div></dl></header>
    <div v-if="!hasData" data-testid="service-health-empty" class="dashboard-empty">暂无服务健康数据</div>
    <div v-else class="health-visual">
      <div ref="chartElement" class="dashboard-chart" :style="{ aspectRatio: '16 / 10', minHeight: '190px' }" />
      <ul class="health-summary" role="list" aria-label="服务健康状态明细"><li v-for="bucket in healthBuckets" :key="bucket.label" role="listitem"><span><i aria-hidden="true" />{{ bucket.label }}</span><strong>{{ bucket.value.toLocaleString("zh-CN") }}</strong></li></ul>
    </div>
    <ul v-if="!hasData && healthBuckets.length" class="sr-only"><li v-for="bucket in healthBuckets" :key="bucket.label">{{ bucket.label }}：{{ bucket.value }}</li></ul>
  </section>
</template>

<style scoped>
.service-health-panel { display: grid; gap: 10px; min-width: 0; background: transparent; }
header { display: flex; justify-content: space-between; align-items: start; gap: 16px; }
h3 { margin: 0; color: #1f2937; font-size: 15px; line-height: 22px; }
dl { display: flex; gap: 0; margin: 0; }
dl > div { min-width: 72px; padding: 0 10px; }
dl > div + div { border-left: 1px solid #dfe3e8; }
dl > div:last-child { padding-right: 0; }
dt { color: #4b5563; font-size: 12px; }
dd { margin: 3px 0 0; color: #1f2937; font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; text-align: right; }
.health-visual { display: grid; grid-template-columns: minmax(0, 1fr) minmax(104px, .58fr); align-items: center; gap: 12px; min-width: 0; }
.dashboard-chart { width: 100%; }
.health-summary { display: grid; align-content: center; min-width: 0; padding: 0; margin: 0; list-style: none; }
.health-summary li { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 0; color: #4b5563; font-size: 12px; }
.health-summary li + li { border-top: 1px solid #dfe3e8; }
.health-summary span { display: flex; min-width: 0; align-items: center; gap: 6px; overflow-wrap: anywhere; }
.health-summary i { width: 7px; height: 7px; flex: 0 0 7px; background: #6b7280; border-radius: 50%; }
.health-summary li:nth-child(5n + 1) i { background: #2563eb; }
.health-summary li:nth-child(5n + 2) i { background: #16835b; }
.health-summary li:nth-child(5n + 3) i { background: #b26a00; }
.health-summary li:nth-child(5n + 4) i { background: #c2413a; }
.health-summary strong { flex: 0 0 auto; color: #1f2937; font-size: 13px; font-variant-numeric: tabular-nums; }
.dashboard-empty { display: grid; min-height: 190px; padding: 16px; place-items: center; color: #4b5563; font-size: 13px; text-align: center; background: rgb(255 255 255 / 34%); border: 0; border-radius: 6px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }

@media (max-width: 520px) {
  header { flex-wrap: wrap; }
  .health-visual { grid-template-columns: minmax(0, 1fr); }
}
</style>
