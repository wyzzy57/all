<script setup lang="ts">
import { computed } from "vue";

import {
  unavailableSources,
  type LlmObservabilitySummary,
} from "./llmMetricCatalog";

const props = defineProps<{
  summary: LlmObservabilitySummary;
  mlflowUrl?: string;
  tensorboardUrl?: string;
}>();

const degraded = computed(() => unavailableSources(props.summary.availability));
const percent = computed(() => Number(props.summary.progress.percent ?? 0));

function value(record: Record<string, unknown>, key: string, fallback = "-") {
  const result = record[key];
  return result === undefined || result === null || result === "" ? fallback : String(result);
}

function duration(seconds: unknown) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "-";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return `${hours} 小时 ${minutes} 分钟`;
}

function metric(value: number | undefined) {
  if (value === undefined || !Number.isFinite(value)) return "-";
  if (Math.abs(value) < 0.001 && value !== 0) return value.toExponential(3);
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}
</script>

<template>
  <section class="llm-overview" aria-label="大模型训练概览">
    <aside v-if="degraded.length" class="source-degradation" data-testid="source-degradation">
      <strong>部分数据源暂不可用，已展示可用的降级数据。</strong>
      <span v-for="source in degraded" :key="source.key">
        {{ source.label }}<template v-if="source.reason">：{{ source.reason }}</template>
      </span>
    </aside>

    <div class="overview-grid">
      <article class="progress-panel">
        <header>
          <div>
            <span class="eyebrow">当前训练</span>
            <h3>{{ summary.pipeline_name }}</h3>
          </div>
          <span class="status">{{ summary.status }}</span>
        </header>
        <div class="progress-value" data-testid="llm-progress">
          <strong>{{ percent.toFixed(0) }}%</strong>
          <span>Step {{ value(summary.progress, "current_step", "0") }} / {{ value(summary.progress, "total_steps", "-") }}</span>
        </div>
        <div class="progress-track" aria-hidden="true">
          <span :style="{ width: `${Math.min(100, Math.max(0, percent))}%` }" />
        </div>
        <dl class="timing-grid">
          <div><dt>Epoch</dt><dd>{{ value(summary.progress, "current_epoch") }} / {{ value(summary.progress, "total_epochs") }}</dd></div>
          <div><dt>已运行</dt><dd>{{ duration(summary.timing.elapsed_seconds) }}</dd></div>
          <div><dt>预计剩余</dt><dd>{{ duration(summary.timing.eta_seconds) }}</dd></div>
        </dl>
      </article>

      <article class="context-panel">
        <h3>训练上下文</h3>
        <dl>
          <div><dt>基础模型</dt><dd>{{ value(summary.environment, "model_id") }}</dd></div>
          <div><dt>数据集</dt><dd>{{ value(summary.environment, "dataset_name") }}</dd></div>
          <div><dt>执行节点</dt><dd>{{ value(summary.environment, "node_name") }}</dd></div>
          <div><dt>任务 ID</dt><dd>{{ summary.job_id }}</dd></div>
        </dl>
      </article>
    </div>

    <div class="metric-strip">
      <article v-for="(value, key) in summary.latest_metrics" :key="key">
        <span>{{ key }}</span>
        <strong>{{ metric(value) }}</strong>
      </article>
    </div>

    <footer v-if="mlflowUrl || tensorboardUrl" class="external-actions">
      <span>外部工具</span>
      <a v-if="mlflowUrl" data-testid="open-mlflow" :href="mlflowUrl" target="_blank" rel="noopener noreferrer">MLflow</a>
      <a v-if="tensorboardUrl" data-testid="open-tensorboard" :href="tensorboardUrl" target="_blank" rel="noopener noreferrer">TensorBoard</a>
    </footer>
  </section>
</template>

<style scoped>
.llm-overview { display: grid; gap: 16px; color: #172033; }
.source-degradation { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; padding: 10px 12px; border: 1px solid #fde3b0; background: #fffbeb; color: #8a5a09; font-size: 12px; }
.source-degradation strong { font-weight: 600; }
.overview-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(280px, .9fr); gap: 16px; }
.progress-panel, .context-panel { border: 1px solid #dfe5ed; background: #fff; padding: 18px; }
.progress-panel header { display: flex; justify-content: space-between; gap: 16px; }
h3 { margin: 0; font-size: 16px; }
.eyebrow, dt, .metric-strip span, .external-actions > span { color: #667085; font-size: 12px; }
.eyebrow { display: block; margin-bottom: 5px; }
.status { align-self: flex-start; padding: 3px 8px; background: #ecfdf3; color: #087443; font-size: 12px; }
.progress-value { display: flex; align-items: baseline; gap: 14px; margin-top: 24px; }
.progress-value strong { font-size: 32px; line-height: 1; }
.progress-value span { color: #475467; font-size: 13px; }
.progress-track { height: 8px; margin-top: 12px; overflow: hidden; background: #eef2f6; }
.progress-track span { display: block; height: 100%; background: #2f7df6; transition: width .2s ease; }
.timing-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 20px 0 0; }
.timing-grid div, .context-panel dl div { min-width: 0; }
dd { margin: 5px 0 0; overflow: hidden; font-size: 13px; font-variant-numeric: tabular-nums; text-overflow: ellipsis; white-space: nowrap; }
.context-panel dl { display: grid; gap: 15px; margin: 18px 0 0; }
.metric-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); border: 1px solid #dfe5ed; background: #fff; }
.metric-strip article { display: grid; gap: 5px; padding: 14px 16px; border-right: 1px solid #edf0f4; }
.metric-strip strong { font-size: 18px; font-variant-numeric: tabular-nums; }
.external-actions { display: flex; justify-content: flex-end; gap: 14px; align-items: center; }
.external-actions a { color: #1769e0; font-size: 13px; text-decoration: none; }
@media (max-width: 860px) { .overview-grid { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .timing-grid { grid-template-columns: 1fr; } }
</style>
