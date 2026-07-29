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
let chart: ReturnType<typeof init> | null = null;
let resizeObserver: ResizeObserver | null = null;
let hasAnimated = false;
function escapeHtml(value: string) { return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]!); }
function option(animation: boolean) { return { animation, animationDuration: 360, aria: { enabled: true }, color: ["#20b26b", "#f5b638", "#e56868", "#8b98aa"], tooltip: { trigger: "item", formatter: (params: { name: string; value: number; marker?: string }) => `${params.marker ?? ""}${escapeHtml(params.name)}: ${Number(params.value).toLocaleString("zh-CN")}` }, legend: { type: "scroll", bottom: 0, data: props.healthBuckets.map((item) => item.label), textStyle: { color: "#5c6b80", fontSize: 12 } }, series: [{ type: "pie", radius: ["42%", "66%"], center: ["50%", "45%"], selectedMode: false, data: props.healthBuckets.map((item) => ({ name: item.label, value: item.value })), label: { show: false }, emphasis: { scale: true, scaleSize: 8, label: { show: false } } }] }; }
function disposeChart() { resizeObserver?.disconnect(); resizeObserver = null; chart?.dispose(); chart = null; }
function renderChart() { if (!hasData.value) { disposeChart(); return; } if (!chart && chartElement.value) { chart = init(chartElement.value); chart.setOption(option(!hasAnimated), true); hasAnimated = true; if (typeof ResizeObserver !== "undefined") { resizeObserver = new ResizeObserver(() => chart?.resize()); resizeObserver.observe(chartElement.value); } return; } chart?.setOption(option(false), true); }
watch(() => props.healthBuckets, renderChart, { deep: true, flush: "post" }); onMounted(renderChart); onBeforeUnmount(disposeChart);
</script>

<template>
  <section class="service-health-panel" aria-label="服务健康状态">
    <header><h3>{{ title }}</h3><dl><div><dt>服务调用</dt><dd>{{ calls.toLocaleString("zh-CN") }}</dd></div><div><dt>运行实例</dt><dd>{{ instances.toLocaleString("zh-CN") }}</dd></div></dl></header>
    <div v-if="!hasData" data-testid="service-health-empty" class="dashboard-empty">暂无服务健康数据</div>
    <div v-else ref="chartElement" class="dashboard-chart" :style="{ aspectRatio: '16 / 10', minHeight: '220px' }" />
    <ul class="sr-only"><li v-for="bucket in healthBuckets" :key="bucket.label">{{ bucket.label }}：{{ bucket.value }}</li></ul>
  </section>
</template>

<style scoped>
.service-health-panel { min-width: 0; background: #fff; } header { display: flex; justify-content: space-between; align-items: start; gap: 16px; } h3 { margin: 0; color: #18263b; font-size: 15px; line-height: 22px; } dl { display: flex; gap: 18px; margin: 0; } dt { color: #7a8799; font-size: 12px; } dd { margin: 3px 0 0; color: #26364e; font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; text-align: right; } .dashboard-chart { width: 100%; } .dashboard-empty { display: grid; min-height: 220px; place-items: center; color: #8b98aa; font-size: 13px; border: 1px dashed #dbe4f1; border-radius: 6px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
