<template>
  <div class="resource-grid">
    <div v-for="item in items" :key="item.label" class="resource-item">
      <span>{{ item.label }}</span><strong>{{ item.value }}</strong>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import type { ComputeNodeRecord } from "@/api/client";

const props = defineProps<{ node: ComputeNodeRecord }>();

const metric = (key: string): number | null => {
  const value = props.node.resources[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
};
const valueOrDash = (value: number | null) => value === null ? "-" : String(value);
const percent = (key: string) => {
  const value = metric(key);
  return value === null ? "暂无数据" : `${value.toFixed(1)}%`;
};
const gib = (value: number | null, divisor: number) =>
  value === null ? "-" : `${(value / divisor).toFixed(1)} GiB`;
const temperatureAndPower = () => {
  const temperature = metric("gpu_temperature_celsius");
  const power = metric("gpu_power_draw_watts");
  if (temperature === null && power === null) return "暂无数据";
  const temperatureText = temperature === null ? "-" : `${temperature}°C`;
  const powerText = power === null ? "-" : `${power.toFixed(0)} W`;
  return `${temperatureText} · ${powerText}`;
};

const items = computed(() => [
  { label: "CPU", value: `${valueOrDash(metric("cpu_logical_cores"))} 核 · ${percent("cpu_utilization_percent")}` },
  { label: "内存可用", value: gib(metric("memory_available_kib"), 1024 * 1024) },
  { label: "磁盘可用", value: gib(metric("disk_available_bytes"), 1024 ** 3) },
  { label: "GPU", value: `${valueOrDash(metric("gpu_count"))} 张 · ${percent("gpu_utilization_percent")}` },
  { label: "显存", value: `${valueOrDash(metric("gpu_memory_used_mib"))} / ${valueOrDash(metric("gpu_memory_total_mib"))} MiB` },
  { label: "温度 / 功耗", value: temperatureAndPower() },
]);
</script>

<style scoped>
.resource-grid { display: grid; grid-template-columns: repeat(3, minmax(150px, 1fr)); gap: 12px 24px; }
.resource-item { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.resource-item span { color: #667085; font-size: 12px; }
.resource-item strong { color: #172033; font-size: 14px; font-weight: 600; }
@media (max-width: 900px) { .resource-grid { grid-template-columns: repeat(2, minmax(140px, 1fr)); } }
</style>
