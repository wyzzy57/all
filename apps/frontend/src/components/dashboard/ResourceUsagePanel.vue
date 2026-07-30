<script setup lang="ts">
export type ResourceUsageValue = {
  label: string;
  value: number | null;
  unit?: string;
  available: boolean;
  unavailable?: boolean;
};

defineProps<{
  usage: ResourceUsageValue[];
  gpuSeries?: ResourceUsageValue[];
  stale?: boolean;
}>();

function hasUsableValue(item: ResourceUsageValue): item is ResourceUsageValue & { value: number; available: true } {
  return item.available && item.value !== null && Number.isFinite(item.value);
}

function formatValue(item: ResourceUsageValue) {
  if (!hasUsableValue(item)) return "暂无遥测数据";
  return `${item.value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}${item.unit ?? ""}`;
}

function percentage(item: ResourceUsageValue) {
  return hasUsableValue(item) ? Math.max(0, Math.min(100, item.value)) : 0;
}

function meterValue(item: ResourceUsageValue) {
  return hasUsableValue(item) ? percentage(item) : undefined;
}
</script>

<template>
  <section class="resource-usage-panel" :style="{ minHeight: '240px' }" aria-label="资源使用率">
    <header><h3>资源使用率</h3><span v-if="stale" data-testid="resource-stale" class="stale-indicator" role="status">遥测已逐渐变旧</span></header>
    <p v-if="usage.length === 0 && (gpuSeries?.length ?? 0) === 0" data-testid="resource-empty" class="resource-empty">暂无资源遥测数据</p>
    <div v-else class="resource-grid">
      <div v-for="item in usage" :key="item.label" class="resource-row">
        <div class="resource-label"><span>{{ item.label }}</span><span class="resource-reading"><strong>{{ formatValue(item) }}</strong><small v-if="item.available && item.unavailable">部分遥测不可用</small></span></div>
        <div class="usage-track" role="meter" :aria-label="item.label" :aria-valuenow="meterValue(item)" :aria-valuetext="formatValue(item)" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${percentage(item)}%` }" :class="{ unavailable: !hasUsableValue(item) }" /></div>
      </div>
      <div v-for="gpu in gpuSeries" :key="gpu.label" class="resource-row gpu-row">
        <div class="resource-label"><span>{{ gpu.label }}</span><span class="resource-reading"><strong>{{ formatValue(gpu) }}</strong><small v-if="gpu.available && gpu.unavailable">部分遥测不可用</small></span></div>
        <div class="usage-track" role="meter" :aria-label="gpu.label" :aria-valuenow="meterValue(gpu)" :aria-valuetext="formatValue(gpu)" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${percentage(gpu)}%` }" :class="{ unavailable: !hasUsableValue(gpu) }" /></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.resource-usage-panel { display: grid; align-content: start; gap: 14px; min-width: 0; background: transparent; }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h3 { margin: 0; color: #1f2937; font-size: 15px; line-height: 22px; }
.stale-indicator { color: #7a4b00; font-size: 12px; }
.resource-grid { display: grid; gap: 12px; }
.resource-row { display: grid; gap: 7px; min-width: 0; }
.resource-label { display: flex; min-width: 0; align-items: start; justify-content: space-between; gap: 12px; color: #4b5563; font-size: 13px; }
.resource-label > span:first-child { min-width: 0; overflow-wrap: anywhere; }
.resource-reading { display: grid; flex: 0 1 auto; justify-items: end; min-width: 0; text-align: right; }
.resource-reading strong { min-width: 0; color: #1f2937; font-weight: 600; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.resource-reading small { color: #7a4b00; font-size: 12px; line-height: 16px; }
.usage-track { height: 8px; overflow: hidden; background: #dfe3e8; border-radius: 4px; }
.usage-track i { display: block; height: 100%; background: #4f6f8f; border-radius: inherit; transition: width 220ms ease; }
.usage-track i.unavailable { width: 100% !important; background: repeating-linear-gradient(135deg, #cbd2da 0 6px, #eef0f3 6px 12px); }
.gpu-row .usage-track i { background: #426789; }
.resource-empty { display: grid; min-height: 180px; margin: 0; padding: 16px; place-items: center; color: #4b5563; font-size: 13px; text-align: center; background: rgb(255 255 255 / 34%); border: 0; border-radius: 6px; }

@media (prefers-reduced-motion: reduce) {
  .usage-track i { transition: none; }
}
</style>
