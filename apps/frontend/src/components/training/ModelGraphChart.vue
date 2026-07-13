<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { GraphChart } from "echarts/charts";
import { LegendComponent, ToolboxComponent, TooltipComponent } from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { TrainingObservabilityGraph } from "@/api/client";

const props = defineProps<{
  nodes: TrainingObservabilityGraph["nodes"];
  edges: TrainingObservabilityGraph["edges"];
  height?: string | number;
}>();

use([CanvasRenderer, GraphChart, TooltipComponent, LegendComponent, ToolboxComponent]);

const chartElement = ref<HTMLElement | null>(null);
const chartHeight = computed(() => (typeof props.height === "number" ? `${props.height}px` : (props.height ?? "420px")));
let chart: ReturnType<typeof init> | null = null;

function option() {
  return {
    animation: false,
    tooltip: {},
    legend: { show: false },
    toolbox: { right: 8, feature: { restore: {}, saveAsImage: {} } },
    series: [
      {
        type: "graph",
        layout: "force",
        roam: true,
        force: { repulsion: 180, edgeLength: 90 },
        label: { show: true, position: "right" },
        data: props.nodes.map((node) => ({
          id: node.id,
          name: node.label,
          op: node.op,
          attributes: node.attributes,
          value: node.op
        })),
        links: props.edges
      }
    ]
  };
}

function renderChart() {
  chart?.setOption(option(), true);
}

function resizeChart() {
  chart?.resize();
}

watch(() => [props.nodes, props.edges], renderChart, { deep: true });

onMounted(() => {
  chart = init(chartElement.value!);
  renderChart();
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
  min-height: 280px;
}
</style>
