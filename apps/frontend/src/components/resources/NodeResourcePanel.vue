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
const number = (key: string) => Number(props.node.resources[key] ?? 0);
const percent = (key: string) => `${number(key).toFixed(1)}%`;
const gib = (value: number) => value ? `${(value / 1024 / 1024).toFixed(1)} GiB` : "-";
const diskGib = (value: number) => value ? `${(value / 1024 ** 3).toFixed(1)} GiB` : "-";

const items = computed(() => [
  { label: "CPU", value: `${number("cpu_logical_cores")} 核 · ${percent("cpu_utilization_percent")}` },
  { label: "内存可用", value: gib(number("memory_available_kib")) },
  { label: "磁盘可用", value: diskGib(number("disk_available_bytes")) },
  { label: "GPU", value: `${number("gpu_count")} 张 · ${percent("gpu_utilization_percent")}` },
  { label: "显存", value: `${number("gpu_memory_used_mib")} / ${number("gpu_memory_total_mib")} MiB` },
  { label: "温度 / 功耗", value: `${number("gpu_temperature_celsius")}°C · ${number("gpu_power_draw_watts").toFixed(0)} W` },
]);
</script>

<style scoped>
.resource-grid { display: grid; grid-template-columns: repeat(3, minmax(150px, 1fr)); gap: 12px 24px; }
.resource-item { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.resource-item span { color: #667085; font-size: 12px; }
.resource-item strong { color: #172033; font-size: 14px; font-weight: 600; }
@media (max-width: 900px) { .resource-grid { grid-template-columns: repeat(2, minmax(140px, 1fr)); } }
</style>
