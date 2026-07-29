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
    <p v-if="items.length === 0" data-testid="summary-empty" class="summary-empty">暂无统计数据</p>
    <div v-for="item in items" :key="item.label" class="summary-item" role="listitem">
      <span class="summary-label">{{ item.label }}</span>
      <strong class="summary-value">{{ displayValue(item.value) }}<small v-if="item.unit">{{ item.unit }}</small></strong>
    </div>
  </section>
</template>

<style scoped>
.statistic-summary-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0;
  min-height: 78px;
  background: #fff;
  border: 1px solid #e7edf6;
  border-radius: 6px;
}

.summary-item {
  display: grid;
  align-content: center;
  gap: 6px;
  min-width: 0;
  padding: 14px 20px;
  border-right: 1px solid #edf1f7;
}

.summary-item:last-child { border-right: 0; }
.summary-label { color: #66758c; font-size: 13px; line-height: 18px; }
.summary-value { color: #18263b; font-size: 24px; line-height: 28px; font-variant-numeric: tabular-nums; }
.summary-value small { margin-left: 4px; color: #66758c; font-size: 13px; font-weight: 500; }
.summary-empty { grid-column: 1 / -1; margin: 0; padding: 27px 20px; color: #8b98aa; font-size: 13px; text-align: center; }

@media (max-width: 720px) {
  .summary-item:nth-child(n) { border-right: 0; border-bottom: 1px solid #edf1f7; }
  .summary-item:last-child { border-bottom: 0; }
}
</style>
