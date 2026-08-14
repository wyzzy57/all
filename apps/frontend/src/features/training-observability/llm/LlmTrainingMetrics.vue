<script setup lang="ts">
import { computed, ref } from "vue";

import MetricLineChart from "@/components/training/MetricLineChart.vue";
import {
  buildLlmMetricCharts,
  unavailableSources,
  type LlmObservabilityAvailability,
  type LlmScalarSeries,
} from "./llmMetricCatalog";

const props = defineProps<{
  response: { series: LlmScalarSeries; availability: LlmObservabilityAvailability };
}>();

const hidden = ref(new Set<string>());
const groups = computed(() => buildLlmMetricCharts(props.response.series));
const degraded = computed(() => unavailableSources(props.response.availability));

function visibleSeries(series: LlmScalarSeries, sourceKeys: string[]) {
  const result: LlmScalarSeries = {};
  Object.entries(series).forEach(([label, points], index) => {
    if (!hidden.value.has(sourceKeys[index])) result[label] = points;
  });
  return result;
}

function toggle(key: string) {
  const next = new Set(hidden.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  hidden.value = next;
}

function sourceLabel(key: string) {
  return ({
    loss: "训练 Loss",
    eval_loss: "验证 Loss",
    learning_rate: "学习率",
    grad_norm: "梯度范数",
    tokens_per_second: "Tokens/s",
    samples_per_second: "Samples/s",
    epoch: "Epoch",
    step: "Step",
  } as Record<string, string>)[key] ?? key;
}
</script>

<template>
  <section class="llm-metrics" :class="{ 'is-empty': groups.length === 0 }" aria-label="大模型训练指标">
    <aside v-if="degraded.length" class="source-degradation" data-testid="source-degradation">
      <strong>数据源降级</strong>
      <span v-for="source in degraded" :key="source.key">{{ source.label }}<template v-if="source.reason">：{{ source.reason }}</template></span>
    </aside>

    <div v-if="groups.length" class="metric-groups">
      <section v-for="group in groups" :key="group.id" class="metric-group" :data-testid="`metric-group-${group.id}`">
        <header><h3>{{ group.title }}</h3></header>
        <div class="chart-grid">
          <article v-for="chart in group.charts" :key="chart.id" class="chart-panel">
            <div class="chart-heading">
              <div><strong>{{ chart.title }}</strong><span>{{ chart.unit }}</span></div>
              <div class="series-toggles" aria-label="指标显隐">
                <button
                  v-for="key in chart.sourceKeys"
                  :key="key"
                  type="button"
                  :data-testid="`toggle-${key}`"
                  :aria-pressed="!hidden.has(key)"
                  @click="toggle(key)"
                >
                  <span />{{ sourceLabel(key) }}
                </button>
              </div>
            </div>
            <MetricLineChart
              :series="visibleSeries(chart.series, chart.sourceKeys)"
              :unit="chart.unit"
              :axis-min="chart.axisMin"
              :axis-max="chart.axisMax"
              :value-format="chart.valueFormat"
              height="280px"
            />
          </article>
        </div>
      </section>
    </div>
    <div v-else class="empty-state" data-testid="metrics-empty">
      <strong>暂无指标数据</strong>
      <span>训练开始产生遥测数据后，指标会自动显示在这里。</span>
    </div>
  </section>
</template>

<style scoped>
.llm-metrics { min-height: 360px; color: #172033; }
.source-degradation { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-bottom: 14px; padding: 9px 12px; border: 1px solid #fde3b0; background: #fffbeb; color: #8a5a09; font-size: 12px; }
.metric-groups { display: grid; gap: 20px; }
.metric-group { display: grid; gap: 10px; }
.metric-group header { border-bottom: 1px solid #e7ebf0; padding-bottom: 8px; }
h3 { margin: 0; font-size: 16px; }
.chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.metric-group:first-child .chart-grid { grid-template-columns: 1fr; }
.chart-panel { min-width: 0; overflow: hidden; border: 1px solid #dfe5ed; background: #fff; }
.chart-heading { display: flex; min-height: 54px; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 12px; border-bottom: 1px solid #edf0f4; }
.chart-heading > div:first-child { display: grid; gap: 3px; }
.chart-heading strong { font-size: 13px; }
.chart-heading > div:first-child span { color: #667085; font-size: 11px; }
.series-toggles { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.series-toggles button { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: #475467; cursor: pointer; font: inherit; font-size: 11px; }
.series-toggles button > span { width: 7px; height: 7px; border-radius: 50%; background: #2f7df6; }
.series-toggles button[aria-pressed="false"] { color: #98a2b3; text-decoration: line-through; }
.series-toggles button[aria-pressed="false"] > span { background: #c8ced8; }
.empty-state { display: grid; min-height: 360px; place-content: center; gap: 8px; text-align: center; }
.empty-state span { color: #667085; font-size: 13px; }
@media not all { .chart-grid { grid-template-columns: 1fr; } }
</style>
