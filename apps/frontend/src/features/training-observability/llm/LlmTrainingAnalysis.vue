<script setup lang="ts">
import { computed } from "vue";

import {
  unavailableSources,
  type LlmAnalysisResponse,
  type LlmArtifactsResponse,
} from "./llmMetricCatalog";

const props = defineProps<{
  analysis: LlmAnalysisResponse;
  /** Kept for the shared view contract; artifacts render in the dedicated tab. */
  artifacts?: LlmArtifactsResponse;
}>();

const degraded = computed(() => unavailableSources(props.analysis.availability));

function evidence(values: Record<string, number | null>) {
  return Object.entries(values).map(([key, value]) => `${key}: ${value ?? "-"}`).join(" · ");
}
</script>

<template>
  <section class="llm-analysis" aria-label="大模型训练分析">
    <aside v-if="degraded.length" class="source-degradation" data-testid="source-degradation">
      <strong>分析数据不完整</strong>
      <span v-for="source in degraded" :key="source.key">{{ source.label }}<template v-if="source.reason">：{{ source.reason }}</template></span>
    </aside>

    <section class="analysis-section">
      <header><h3>训练分析</h3><span>仅展示有明确指标证据的确定性结论</span></header>
      <div v-if="analysis.findings.length" class="finding-list">
        <article
          v-for="finding in analysis.findings"
          :key="finding.code"
          class="finding"
          :class="`severity-${finding.severity}`"
          :data-testid="`analysis-finding-${finding.code}`"
        >
          <div class="finding-title"><strong>{{ finding.title }}</strong><span>{{ finding.severity }}</span></div>
          <p>{{ finding.message }}</p>
          <dl>
            <div><dt>指标</dt><dd>{{ finding.metric_names.join("、") || "-" }}</dd></div>
            <div><dt>范围</dt><dd>{{ finding.step_range ? `Step ${finding.step_range[0]} - ${finding.step_range[1]}` : "证据范围不足" }}</dd></div>
          </dl>
          <div class="evidence" :data-testid="`analysis-evidence-${finding.code}`">{{ evidence(finding.observed_values) }}</div>
        </article>
      </div>
      <div v-else class="compact-empty">当前没有达到诊断阈值的异常证据。</div>
    </section>

    <!-- Training files are presented in the dedicated 产物 tab. -->
    <!--
      <section class="artifact-section">
        <header><h3>Checkpoints</h3><span>{{ checkpoints.length }}</span></header>
        <ul v-if="checkpoints.length" data-testid="checkpoint-list">
          <li v-for="artifact in checkpoints" :key="artifact.path">
            <button type="button" :data-testid="`artifact-${artifact.path}`" @click="emit('open-artifact', artifact)">
              <span><strong>{{ fileName(artifact.path) }}</strong><small>{{ artifact.path }}</small></span>
              <span>{{ fileSize(artifact.size_bytes) }}</span>
            </button>
          </li>
        </ul>
        <div v-else class="compact-empty">暂无 checkpoint。</div>
      </section>

      <section class="artifact-section">
        <header><h3>训练产物</h3><span>{{ otherArtifacts.length }}</span></header>
        <ul v-if="otherArtifacts.length" data-testid="artifact-list">
          <li v-for="artifact in otherArtifacts" :key="artifact.path">
            <button type="button" :data-testid="`artifact-${artifact.path}`" @click="emit('open-artifact', artifact)">
              <span><strong>{{ fileName(artifact.path) }}</strong><small>{{ artifact.path }}</small></span>
              <span>{{ fileSize(artifact.size_bytes) }}</span>
            </button>
          </li>
        </ul>
        <div v-else class="compact-empty">暂无其他训练产物。</div>
      </section>
    -->
  </section>
</template>

<style scoped>
.llm-analysis { display: grid; gap: 20px; color: #172033; }
.source-degradation { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 9px 12px; border: 1px solid #fde3b0; background: #fffbeb; color: #8a5a09; font-size: 12px; }
.analysis-section { border: 1px solid #dfe5ed; background: #fff; }
.analysis-section > header { display: flex; min-height: 48px; align-items: center; justify-content: space-between; gap: 16px; padding: 0 14px; border-bottom: 1px solid #edf0f4; }
h3 { margin: 0; font-size: 15px; }
header span { color: #667085; font-size: 12px; }
.finding-list { display: grid; gap: 10px; padding: 14px; }
.finding { border-left: 3px solid #f0a020; background: #fffcf5; padding: 12px 14px; }
.finding.severity-error, .finding.severity-critical { border-color: #d92d20; background: #fff7f6; }
.finding.severity-info { border-color: #2f7df6; background: #f7faff; }
.finding-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.finding-title span { color: #667085; font-size: 11px; text-transform: uppercase; }
.finding p { margin: 8px 0; color: #475467; font-size: 13px; }
.finding dl { display: flex; flex-wrap: wrap; gap: 8px 24px; margin: 0; }
.finding dl div { display: flex; gap: 6px; }
.finding dt, .finding dd { margin: 0; font-size: 12px; }
.finding dt { color: #667085; }
.evidence { margin-top: 9px; color: #344054; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; }
.compact-empty { padding: 22px 14px; color: #667085; font-size: 13px; }
</style>
