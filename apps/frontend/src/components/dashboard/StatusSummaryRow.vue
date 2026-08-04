<script setup lang="ts">
import { dashboardStatusLabel } from "./statusLabels";

export type StatusSummaryBucket = {
  label: string;
  value: number;
};

defineProps<{
  buckets: StatusSummaryBucket[];
}>();

function statusTone(label: string) {
  const normalized = label.trim().toLowerCase();
  if (/(失败|异常|错误|离线|unhealthy|failed|error|offline)/.test(normalized)) {
    return "status-dot--danger";
  }
  if (/(告警|警告|退化|过期|warning|degraded|stale)/.test(normalized)) {
    return "status-dot--warning";
  }
  if (/(成功|健康|就绪|在线|完成|success|healthy|ready|online|completed)/.test(normalized)) {
    return "status-dot--success";
  }
  if (/(运行|训练|部署|启动|处理中|running|training|deploying|starting|processing)/.test(normalized)) {
    return "status-dot--active";
  }
  return "status-dot--neutral";
}
</script>

<template>
  <div class="status-summary-row">
    <p v-if="buckets.length === 0" data-testid="status-summary-empty" class="status-summary-empty">
      暂无状态数据
    </p>
    <div v-else class="status-summary-list" role="list" aria-label="状态摘要">
      <div v-for="bucket in buckets" :key="bucket.label" class="status-summary-item" role="listitem">
        <span class="status-summary-label">
          <i class="status-dot" :class="statusTone(bucket.label)" aria-hidden="true" />
          <span>{{ dashboardStatusLabel(bucket.label, "pipeline") }}</span>
        </span>
        <strong>{{ bucket.value.toLocaleString("zh-CN") }}</strong>
      </div>
    </div>
  </div>
</template>

<style scoped>
.status-summary-row {
  min-width: 0;
}

.status-summary-list {
  display: flex;
  min-width: 0;
}

.status-summary-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex: 1 1 0;
  min-width: 0;
  min-height: 56px;
  padding: 10px 18px;
  border-right: 1px solid #dde1e5;
}

.status-summary-item:last-child {
  border-right: 0;
}

.status-summary-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #5f6873;
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.status-summary-label > span {
  min-width: 0;
  overflow-wrap: anywhere;
}

.status-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #89929c;
}

.status-dot--success { background: #4f8a68; }
.status-dot--warning { background: #b1812d; }
.status-dot--danger { background: #b65f61; }
.status-dot--active { background: #4f78a8; }
.status-dot--neutral { background: #89929c; }

strong {
  flex: 0 0 auto;
  color: #252a31;
  font-size: 19px;
  line-height: 24px;
  font-variant-numeric: tabular-nums;
}

.status-summary-empty {
  display: grid;
  min-height: 76px;
  place-items: center;
  margin: 0;
  padding: 18px;
  color: #5f6873;
  background: #f5f6f7;
  font-size: 13px;
  line-height: 20px;
  text-align: center;
  overflow-wrap: anywhere;
}

@media (max-width: 520px) {
  .status-summary-list {
    flex-direction: column;
  }

  .status-summary-item {
    border-right: 0;
    border-bottom: 1px solid #dde1e5;
  }

  .status-summary-item:last-child {
    border-bottom: 0;
  }
}
</style>
