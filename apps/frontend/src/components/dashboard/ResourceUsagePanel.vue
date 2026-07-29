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

function formatValue(item: ResourceUsageValue) {
  if (!item.available || item.value === null || !Number.isFinite(item.value)) return "暂无遥测数据";
  return `${item.value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}${item.unit ?? ""}`;
}

function percentage(item: ResourceUsageValue) {
  return item.available && item.value !== null ? Math.max(0, Math.min(100, item.value)) : 0;
}
</script>

<template>
  <section class="resource-usage-panel" :style="{ minHeight: '240px' }" aria-label="资源使用率">
    <header><h3>资源使用率</h3><span v-if="stale" data-testid="resource-stale" class="stale-indicator">遥测已逐渐变旧</span></header>
    <p v-if="usage.length === 0 && (gpuSeries?.length ?? 0) === 0" data-testid="resource-empty" class="resource-empty">暂无资源遥测数据</p>
    <div v-else class="resource-grid">
      <div v-for="item in usage" :key="item.label" class="resource-row">
        <div class="resource-label"><span>{{ item.label }}</span><strong>{{ formatValue(item) }}</strong></div>
        <div class="usage-track" role="meter" :aria-label="item.label" :aria-valuenow="item.value ?? undefined" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${percentage(item)}%` }" :class="{ unavailable: !item.available }" /></div>
      </div>
      <div v-for="gpu in gpuSeries" :key="gpu.label" class="resource-row gpu-row">
        <div class="resource-label"><span>{{ gpu.label }}</span><strong>{{ formatValue(gpu) }}</strong></div>
        <div class="usage-track" role="meter" :aria-label="gpu.label" :aria-valuenow="gpu.value ?? undefined" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${percentage(gpu)}%` }" :class="{ unavailable: !gpu.available }" /></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.resource-usage-panel { display: grid; align-content: start; gap: 16px; min-width: 0; background: #fff; } header { display: flex; align-items: center; justify-content: space-between; gap: 12px; } h3 { margin: 0; color: #18263b; font-size: 15px; line-height: 22px; } .stale-indicator { color: #a66c00; font-size: 12px; } .resource-grid { display: grid; gap: 14px; } .resource-row { display: grid; gap: 7px; min-width: 0; } .resource-label { display: flex; justify-content: space-between; gap: 12px; color: #5c6b80; font-size: 13px; } .resource-label strong { color: #26364e; font-weight: 600; font-variant-numeric: tabular-nums; } .usage-track { height: 10px; overflow: hidden; background: #edf1f7; border-radius: 5px; } .usage-track i { display: block; height: 100%; background: #3f7ff5; border-radius: inherit; transition: width 220ms ease; } .usage-track i.unavailable { width: 100% !important; background: repeating-linear-gradient(135deg, #dfe7f2 0 6px, #f4f7fb 6px 12px); } .gpu-row .usage-track i { background: #5b7dcb; } .resource-empty { display: grid; min-height: 180px; margin: 0; place-items: center; color: #8b98aa; font-size: 13px; border: 1px dashed #dbe4f1; border-radius: 6px; }
</style>
