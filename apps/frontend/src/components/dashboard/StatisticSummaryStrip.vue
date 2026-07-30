<script setup lang="ts">
export type StatisticSummaryItem = {
  label: string;
  value: number | string | null | undefined;
  unit?: string;
};

defineProps<{
  items: StatisticSummaryItem[];
}>();

function displayValue(value: StatisticSummaryItem["value"]) {
  if (value === null || value === undefined || value === "") return "-";
  return typeof value === "number" ? value.toLocaleString("zh-CN") : value;
}
</script>

<template>
  <section class="statistic-summary-strip" role="list" aria-label="统计摘要">
    <div class="statistic-summary-grid">
      <p v-if="items.length === 0" data-testid="summary-empty" class="summary-empty">暂无统计数据</p>
      <div v-for="item in items" :key="item.label" class="summary-item" role="listitem">
        <span class="summary-label">{{ item.label }}</span>
        <strong class="summary-value">{{ displayValue(item.value) }}<small v-if="item.unit">{{ item.unit }}</small></strong>
      </div>
    </div>
  </section>
</template>

<style scoped>
.statistic-summary-strip {
  min-width: 0;
  min-height: 78px;
  overflow: hidden;
  background: transparent;
  container: statistic-summary / inline-size;
}

.statistic-summary-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0;
  min-height: 78px;
}

.summary-item {
  display: grid;
  align-content: center;
  gap: 6px;
  min-width: 0;
  padding: 14px 20px;
}

.summary-item + .summary-item { border-left: 1px solid #dfe3e8; }
.summary-item:nth-child(5n + 1) { border-left: 0; }
.summary-item:nth-child(n + 6) { border-top: 1px solid #dfe3e8; }
.summary-label { color: #4b5563; font-size: 13px; line-height: 18px; overflow-wrap: anywhere; }
.summary-value { display: flex; min-width: 0; flex-wrap: wrap; align-items: baseline; color: #1f2937; font-size: 24px; line-height: 28px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.summary-value small { margin-left: 4px; color: #4b5563; font-size: 13px; font-weight: 500; }
.summary-empty { display: grid; grid-column: 1 / -1; min-height: 78px; margin: 0; padding: 16px 20px; place-items: center; color: #4b5563; font-size: 13px; text-align: center; background: rgb(255 255 255 / 34%); border: 0; }

@container statistic-summary (max-width: 720px) {
  .statistic-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .summary-item + .summary-item { border-left: 0; }
  .summary-item:nth-child(even) { border-left: 1px solid #dfe3e8; }
  .summary-item:nth-child(n + 3) { border-top: 1px solid #dfe3e8; }
}

@container statistic-summary (max-width: 420px) {
  .statistic-summary-grid { grid-template-columns: minmax(0, 1fr); }
  .summary-item:nth-child(even) { border-left: 0; }
  .summary-item + .summary-item { border-top: 1px solid #dfe3e8; }
}
</style>
