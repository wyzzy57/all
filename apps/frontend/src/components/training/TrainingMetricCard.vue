<script setup lang="ts">
import { computed } from "vue";

import MetricLineChart from "./MetricLineChart.vue";
import {
  summarizeMetric,
  type MetricValueFormat,
  type TrainingMetricCard,
} from "./trainingMetricCatalog";

const props = defineProps<{
  card: TrainingMetricCard;
  smoothing: number;
}>();

const summary = computed(() => summarizeMetric(
  props.card.series[props.card.primarySeries] ?? [],
  props.card.direction,
));

function formatValue(value: number | undefined, format: MetricValueFormat) {
  if (value === undefined) return "-";
  if (format === "scientific") return value.toExponential(3);
  if (format === "percent") return `${value.toFixed(2)}%`;
  return value.toFixed(4);
}
</script>

<template>
  <article class="training-metric-card" :data-testid="`metric-card-${card.id}`">
    <header class="metric-card-header">
      <div>
        <strong data-testid="metric-card-title">{{ card.title }}</strong>
        <span v-if="card.unit">{{ card.unit }}</span>
      </div>
      <dl class="metric-card-summary">
        <div>
          <dt>最新</dt>
          <dd data-testid="metric-card-latest">{{ formatValue(summary?.latest, card.format) }}</dd>
        </div>
        <div>
          <dt>最佳</dt>
          <dd data-testid="metric-card-best">
            {{ formatValue(summary?.best, card.format) }}
            <small v-if="summary">Step {{ summary.bestStep }}</small>
          </dd>
        </div>
      </dl>
    </header>
    <MetricLineChart
      :series="card.series"
      :unit="card.unit"
      :axis-min="card.axis?.min"
      :axis-max="card.axis?.max"
      :value-format="card.format"
      :smoothing="smoothing"
      height="270px"
    />
  </article>
</template>

<style scoped>
.training-metric-card {
  min-width: 0;
  overflow: hidden;
  border: 1px solid #dfe5ed;
  border-radius: 6px;
  background: #fff;
}

.metric-card-header {
  display: flex;
  min-height: 64px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
  border-bottom: 1px solid #edf0f4;
}

.metric-card-header > div:first-child {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.metric-card-header strong {
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-card-header span,
.metric-card-summary dt,
.metric-card-summary small {
  color: #667085;
  font-size: 11px;
}

.metric-card-summary {
  display: flex;
  flex: 0 0 auto;
  gap: 20px;
  margin: 0;
}

.metric-card-summary > div {
  display: grid;
  gap: 3px;
}

.metric-card-summary dt,
.metric-card-summary dd {
  margin: 0;
}

.metric-card-summary dd {
  display: grid;
  color: #101828;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

@media (max-width: 520px) {
  .metric-card-header { align-items: flex-start; }
  .metric-card-summary { gap: 12px; }
}
</style>
