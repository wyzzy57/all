<script setup lang="ts">
import { computed, ref, watch } from "vue";

import {
  api,
  type TrainingJobRecord,
  type TrainingObservabilityResources,
  type TrainingObservabilityScalars,
} from "@/api/client";
import MetricLineChart from "./MetricLineChart.vue";
import {
  aliasesForCanonicalKey,
  selectCanonicalSeries,
  summarizeMetric,
  type MetricDirection,
  type MetricValueFormat,
} from "./trainingMetricCatalog";

const props = defineProps<{
  jobs: TrainingJobRecord[];
  pipelineNames: Record<string, string>;
  initialJobId?: string;
}>();

type MetricOption = {
  key: string;
  label: string;
  format: MetricValueFormat;
  direction: MetricDirection;
  axis?: { min?: number; max?: number };
  source?: "scalars" | "resources";
};

type ComparisonRow = {
  job: TrainingJobRecord;
  name: string;
  latest: number;
  best: number;
  bestStep: number;
};

const metricOptions: MetricOption[] = [
  { key: "detection.ap50", label: "AP50", format: "ratio", direction: "max", axis: { min: 0, max: 1 } },
  { key: "detection.ap", label: "AP50-95", format: "ratio", direction: "max", axis: { min: 0, max: 1 } },
  { key: "detection.precision", label: "Precision", format: "ratio", direction: "max", axis: { min: 0, max: 1 } },
  { key: "detection.recall", label: "Recall", format: "ratio", direction: "max", axis: { min: 0, max: 1 } },
  { key: "runtime.throughput", label: "Throughput", format: "number", direction: "max" },
  { key: "runtime.elapsed_seconds", label: "Runtime", format: "number", direction: "min" },
  { key: "resource.gpu_memory_peak_mb", label: "Peak GPU memory", format: "number", direction: "min", source: "resources" },
];

const selectedMetricKey = ref(metricOptions[0].key);
const selectedJobIds = ref(initialSelection());
const comparisonSeries = ref<TrainingObservabilityScalars["series"]>({});
const comparisonRows = ref<ComparisonRow[]>([]);
const loading = ref(false);
let requestGeneration = 0;

const selectedMetric = computed(() => metricOptions.find((item) => item.key === selectedMetricKey.value) ?? metricOptions[0]);
const maxSelectionReached = computed(() => selectedJobIds.value.length >= 5);
const hasComparisonData = computed(() => Object.keys(comparisonSeries.value).length >= 2);

function initialSelection() {
  const preferred = props.jobs.find((job) => job.id === props.initialJobId) ?? props.jobs[0];
  const neighbor = props.jobs.find((job) => job.id !== preferred?.id);
  return [preferred?.id, neighbor?.id].filter((value): value is string => Boolean(value));
}

function pipelineName(job: TrainingJobRecord) {
  return props.pipelineNames[job.pipeline_id] ?? job.pipeline_id;
}

function toggleRun(jobId: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  if (checked && !selectedJobIds.value.includes(jobId) && selectedJobIds.value.length < 5) {
    selectedJobIds.value = [...selectedJobIds.value, jobId];
  } else if (!checked) {
    selectedJobIds.value = selectedJobIds.value.filter((id) => id !== jobId);
  }
}

function readParam(job: TrainingJobRecord, key: string) {
  const value = job.params?.[key];
  return value === undefined || value === null || value === "" ? "-" : String(value);
}

function formatValue(value: number) {
  if (selectedMetric.value.format === "scientific") return value.toExponential(3);
  return value.toFixed(4);
}

function peakGpuMemoryPoints(series: TrainingObservabilityResources["series"]) {
  const peaks = new Map<string, TrainingObservabilityResources["series"][string][number]>();
  for (const [key, points] of Object.entries(series)) {
    if (!/^gpu\.[^.]+\.memory_(?:used|peak)_(?:mb|mib)$/i.test(key)) continue;
    for (const point of points) {
      const pointKey = `${point.step}:${point.timestamp}`;
      const existing = peaks.get(pointKey);
      if (!existing || point.value > existing.value) peaks.set(pointKey, point);
    }
  }
  return [...peaks.values()].sort((left, right) => left.step - right.step || left.timestamp - right.timestamp);
}

async function loadComparison() {
  const currentGeneration = ++requestGeneration;
  comparisonSeries.value = {};
  comparisonRows.value = [];
  const jobs = selectedJobIds.value
    .map((id) => props.jobs.find((job) => job.id === id))
    .filter((job): job is TrainingJobRecord => Boolean(job));
  if (jobs.length < 2) {
    loading.value = false;
    return;
  }

  loading.value = true;
  try {
    const settledResponses = await Promise.allSettled(jobs.map(async (job) => ({
      job,
      response: selectedMetric.value.source === "resources"
        ? await api.getTrainingObservabilityResources(job.id, { max_points: 1000 })
        : await api.getTrainingObservabilityScalars(job.id, {
          keys: aliasesForCanonicalKey(selectedMetricKey.value),
          max_points: 1000,
        }),
    })));
    if (currentGeneration !== requestGeneration) return;
    const responses = settledResponses.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);

    const nextSeries: TrainingObservabilityScalars["series"] = {};
    const nextRows: ComparisonRow[] = [];
    const baseNames = responses.map(({ job }) => pipelineName(job));
    const duplicateNameCounts = new Map<string, number>();
    for (const name of baseNames) duplicateNameCounts.set(name, (duplicateNameCounts.get(name) ?? 0) + 1);
    for (const { job, response } of responses) {
      const points = selectedMetric.value.source === "resources"
        ? peakGpuMemoryPoints(response.series)
        : selectCanonicalSeries(response.series, selectedMetricKey.value);
      if (!points.length) continue;
      const baseName = pipelineName(job);
      const name = (duplicateNameCounts.get(baseName) ?? 0) > 1
        ? `${baseName} · ${job.id.slice(0, 8)}`
        : baseName;
      nextSeries[name] = points;
      const summary = summarizeMetric(points, selectedMetric.value.direction);
      if (summary) nextRows.push({ job, name, ...summary });
    }
    comparisonSeries.value = nextSeries;
    comparisonRows.value = nextRows;
  } finally {
    if (currentGeneration === requestGeneration) loading.value = false;
  }
}

watch([selectedJobIds, selectedMetricKey, () => props.jobs], loadComparison, { deep: true, immediate: true });
</script>

<template>
  <section class="run-comparison" data-testid="run-comparison">
    <div class="comparison-controls">
      <label class="metric-select-label">
        <span>对比指标</span>
        <select v-model="selectedMetricKey" data-testid="comparison-metric-select">
          <option v-for="option in metricOptions" :key="option.key" :value="option.key">{{ option.label }}</option>
        </select>
        <small data-testid="comparison-scope-note">框架私有损失不具备统一语义，不能跨框架比较。</small>
      </label>
      <fieldset>
        <legend>训练记录 <span>选择 2-5 条</span></legend>
        <label v-for="job in jobs" :key="job.id" class="run-choice">
          <input
            type="checkbox"
            :checked="selectedJobIds.includes(job.id)"
            :disabled="!selectedJobIds.includes(job.id) && maxSelectionReached"
            :data-testid="`comparison-run-${job.id}`"
            @change="toggleRun(job.id, $event)"
          />
          <span>{{ pipelineName(job) }}</span>
          <small>{{ job.status }}</small>
        </label>
      </fieldset>
    </div>

    <div v-if="loading" class="comparison-state">正在加载对比数据...</div>
    <el-empty v-else-if="!hasComparisonData" description="暂无可对比数据，请选择至少两条包含该指标的训练记录" />
    <template v-else>
      <MetricLineChart
        :series="comparisonSeries"
        :axis-min="selectedMetric.axis?.min"
        :axis-max="selectedMetric.axis?.max"
        :value-format="selectedMetric.format"
        height="360px"
      />
      <div class="comparison-table-wrap">
        <table class="comparison-table">
          <thead>
            <tr><th>训练记录</th><th>状态</th><th>最终值</th><th>最佳值</th><th>最佳 Step</th><th>Epochs</th><th>Batch</th><th>学习率</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in comparisonRows" :key="row.job.id" data-testid="comparison-summary-row">
              <td>{{ row.name }}</td>
              <td>{{ row.job.status }}</td>
              <td>{{ formatValue(row.latest) }}</td>
              <td>{{ formatValue(row.best) }}</td>
              <td>Step {{ row.bestStep }}</td>
              <td>{{ readParam(row.job, "epochs") }}</td>
              <td>{{ readParam(row.job, "batch") }}</td>
              <td>{{ readParam(row.job, "lr0") }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.run-comparison { display: grid; gap: 18px; }
.comparison-controls { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 20px; padding: 16px 0; border-bottom: 1px solid #e3e8ef; }
.metric-select-label { display: grid; align-content: start; gap: 8px; font-size: 13px; }
.metric-select-label select { width: 100%; height: 34px; padding: 0 30px 0 10px; border: 1px solid #cfd6e2; border-radius: 4px; background: #fff; }
fieldset { display: flex; flex-wrap: wrap; gap: 8px 16px; min-width: 0; margin: 0; padding: 0; border: 0; }
legend { width: 100%; margin-bottom: 4px; font-size: 13px; }
legend span { margin-left: 6px; color: #667085; font-size: 11px; }
.run-choice { display: inline-flex; align-items: center; gap: 6px; min-width: 150px; font-size: 13px; cursor: pointer; }
.run-choice small { color: #667085; font-size: 11px; }
.comparison-state { min-height: 180px; padding-top: 64px; color: #667085; text-align: center; }
.comparison-table-wrap { overflow-x: auto; border: 1px solid #e3e8ef; }
.comparison-table { width: 100%; min-width: 780px; border-collapse: collapse; font-size: 12px; }
.comparison-table th, .comparison-table td { padding: 10px 12px; border-bottom: 1px solid #e8ecf2; text-align: left; white-space: nowrap; }
.comparison-table th { background: #f7f8fa; color: #475467; font-weight: 500; }
.comparison-table td { font-variant-numeric: tabular-nums; }
@media (max-width: 700px) { .comparison-controls { grid-template-columns: 1fr; } }
</style>
