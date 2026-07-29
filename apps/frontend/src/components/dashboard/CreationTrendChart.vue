<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { BarChart } from "echarts/charts";
import { AriaComponent, GridComponent, TooltipComponent } from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

export type DashboardTrend = { labels: string[]; values: number[] };
const props = withDefaults(defineProps<{ trend: DashboardTrend; title?: string }>(), { title: "产线创建时间分析" });

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent, AriaComponent]);
const chartElement = ref<HTMLElement | null>(null);
const hasData = computed(() => props.trend.labels.length > 0 && props.trend.values.length > 0);
let chart: ReturnType<typeof init> | null = null;
let resizeObserver: ResizeObserver | null = null;
let hasAnimated = false;

function option(animation: boolean) {
  return { animation, animationDuration: 360, aria: { enabled: true }, grid: { top: 24, left: 12, right: 12, bottom: 28, containLabel: true }, tooltip: { trigger: "axis", valueFormatter: (value: number) => Number(value).toLocaleString("zh-CN") }, xAxis: { type: "category", data: props.trend.labels, axisTick: { alignWithLabel: true }, axisLabel: { color: "#718096", fontSize: 12 } }, yAxis: { type: "value", minInterval: 1, axisLabel: { color: "#718096", fontSize: 12 }, splitLine: { lineStyle: { color: "#edf1f7" } } }, series: [{ type: "bar", data: props.trend.values, barMaxWidth: 28, itemStyle: { color: "#79a8f9", borderRadius: [3, 3, 0, 0] } }] };
}
function disposeChart() { resizeObserver?.disconnect(); resizeObserver = null; chart?.dispose(); chart = null; }
function renderChart() { if (!hasData.value) { disposeChart(); return; } if (!chart && chartElement.value) { chart = init(chartElement.value); chart.setOption(option(!hasAnimated), true); hasAnimated = true; if (typeof ResizeObserver !== "undefined") { resizeObserver = new ResizeObserver(() => chart?.resize()); resizeObserver.observe(chartElement.value); } return; } chart?.setOption(option(false), true); }
watch(() => props.trend, renderChart, { deep: true, flush: "post" }); onMounted(renderChart); onBeforeUnmount(disposeChart);
</script>

<template>
  <section class="dashboard-panel creation-trend-chart" :aria-label="title"><header><h3>{{ title }}</h3></header><div v-if="!hasData" data-testid="creation-trend-empty" class="dashboard-empty">暂无创建趋势数据</div><div v-else ref="chartElement" class="dashboard-chart" :style="{ aspectRatio: '16 / 9', minHeight: '240px' }" /><ul class="sr-only"><li v-for="(label, index) in trend.labels" :key="label">{{ label }}：{{ trend.values[index] ?? 0 }}</li></ul></section>
</template>

<style scoped>
.dashboard-panel { min-width: 0; background: #fff; } h3 { margin: 0; color: #18263b; font-size: 15px; line-height: 22px; } .dashboard-chart { width: 100%; } .dashboard-empty { display: grid; min-height: 240px; place-items: center; color: #8b98aa; font-size: 13px; border: 1px dashed #dbe4f1; border-radius: 6px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
