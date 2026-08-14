<script setup lang="ts">
import { computed } from "vue";

import type { TrainingObservabilityAnalysis, TrainingObservabilityArtifacts } from "@/api/client";
import MetricLineChart from "@/components/training/MetricLineChart.vue";
import { buildPaddleXMetricGroups, type PaddleXScalarSeries } from "./paddlexMetricCatalog";

const props = defineProps<{
  series: PaddleXScalarSeries;
  analysis: TrainingObservabilityAnalysis;
  /** Kept for the shared view contract; artifacts render in the dedicated tab. */
  artifacts?: TrainingObservabilityArtifacts;
}>();
const groups = computed(() => buildPaddleXMetricGroups(props.series));
</script>

<template>
  <section class="paddlex-analysis" aria-label="PaddleX 训练分析">
    <header class="analysis-header">
      <div><strong>PaddleX 训练分析</strong><span>框架语义指标与评估产物</span></div>
    </header>
    <section v-for="group in groups" :key="group.id" class="paddlex-group" :data-testid="`paddlex-group-${group.id}`">
      <header><strong>{{ group.title }}</strong></header>
      <div class="chart-grid">
        <article v-for="chart in group.charts" :key="chart.id" class="chart-cell">
          <strong>{{ chart.title }}</strong><small>{{ chart.unit }}</small>
          <MetricLineChart :series="chart.series" :axis-min="chart.axisMin" :axis-max="chart.axisMax" :value-format="chart.valueFormat" height="260px" />
        </article>
      </div>
    </section>
    <!-- Training files are presented in the dedicated 产物 tab. -->
    <!--
    <section class="paddlex-group">
      <header><strong>训练产物</strong></header>
      <div v-if="artifacts.items.length" class="artifact-list">
        <button v-for="artifact in artifacts.items" :key="artifact.path" type="button" :data-testid="`paddlex-artifact-${artifact.path}`" @click="emit('open-artifact', artifact)">
          <span>{{ artifact.path }}</span><small>{{ artifact.size_bytes }} B</small>
        </button>
      </div>
      <el-empty v-else description="暂无训练产物" />
    </section>
    -->
  </section>
</template>

<style scoped>
.paddlex-analysis { display: grid; gap: 16px; color: #172033; }
.analysis-header, .paddlex-group > header { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 48px; padding: 0 14px; border-bottom: 1px solid #e3e8ef; }
.analysis-header { border: 1px solid #dfe5ed; background: #f8fafc; }
.analysis-header div { display: grid; gap: 3px; }
.analysis-header span, small { color: #667085; font-size: 12px; }
.analysis-header a { color: #1d4ed8; font-size: 13px; text-decoration: none; }
.paddlex-group { border: 1px solid #dfe5ed; background: #fff; }
.chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 14px; }
.chart-cell { display: grid; min-width: 0; gap: 4px; border: 1px solid #e3e8ef; padding: 12px; }
.chart-cell > strong { font-size: 13px; }
@media not all { .chart-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
