<script setup lang="ts">
import { computed } from "vue";

import MetricLineChart from "@/components/training/MetricLineChart.vue";
import {
  buildLlmResourceCharts,
  unavailableSources,
  type LlmObservabilityAvailability,
  type LlmScalarSeries,
} from "./llmMetricCatalog";

const props = defineProps<{
  response: { series: LlmScalarSeries; availability: LlmObservabilityAvailability };
}>();

const groups = computed(() => buildLlmResourceCharts(props.response.series));
const degraded = computed(() => unavailableSources(props.response.availability));
</script>

<template>
  <section class="llm-resources" aria-label="大模型训练资源">
    <aside v-if="degraded.length" class="source-degradation" data-testid="source-degradation">
      <strong>资源数据源降级</strong>
      <span v-for="source in degraded" :key="source.key">{{ source.label }}<template v-if="source.reason">：{{ source.reason }}</template></span>
    </aside>
    <div v-if="groups.length" class="resource-groups">
      <section v-for="group in groups" :key="group.id" class="resource-group" :data-testid="`resource-group-${group.id}`">
        <header>
          <h3>{{ group.title }}</h3>
          <span v-if="group.id === 'gpu'">每块 GPU 独立展示，不合并采样</span>
        </header>
        <div class="chart-grid">
          <article v-for="chart in group.charts" :key="chart.id" class="chart-panel">
            <div class="chart-title"><strong>{{ chart.title }}</strong><span>{{ chart.unit }}</span></div>
            <MetricLineChart
              :series="chart.series"
              :unit="chart.unit"
              :axis-min="chart.axisMin"
              :axis-max="chart.axisMax"
              height="270px"
            />
          </article>
        </div>
      </section>
    </div>
    <div v-else class="empty-state" data-testid="resources-empty">
      <strong>暂无资源采样</strong>
      <span>远程训练节点上报采样后，这里将显示 CPU、内存和逐 GPU 曲线。</span>
    </div>
  </section>
</template>

<style scoped>
.llm-resources { min-height: 360px; color: #172033; }
.source-degradation { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-bottom: 14px; padding: 9px 12px; border: 1px solid #fde3b0; background: #fffbeb; color: #8a5a09; font-size: 12px; }
.resource-groups { display: grid; gap: 22px; }
.resource-group { display: grid; gap: 10px; }
.resource-group > header { display: flex; align-items: baseline; justify-content: space-between; border-bottom: 1px solid #e7ebf0; padding-bottom: 8px; }
h3 { margin: 0; font-size: 16px; }
.resource-group > header span { color: #667085; font-size: 12px; }
.chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.chart-panel { min-width: 0; overflow: hidden; border: 1px solid #dfe5ed; background: #fff; }
.chart-title { display: flex; min-height: 44px; align-items: center; justify-content: space-between; padding: 0 12px; border-bottom: 1px solid #edf0f4; }
.chart-title strong { font-size: 13px; }
.chart-title span { color: #667085; font-size: 11px; }
.empty-state { display: grid; min-height: 360px; place-content: center; gap: 8px; text-align: center; }
.empty-state span { color: #667085; font-size: 13px; }
@media (max-width: 920px) { .chart-grid { grid-template-columns: 1fr; } }
</style>
