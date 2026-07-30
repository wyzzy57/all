<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  training: number;
  deployments: number;
  anomalies: number;
}>();

const activities = computed(() => [
  { label: "训练中的任务", value: props.training },
  { label: "部署中的服务", value: props.deployments },
  { label: "异常提醒", value: props.anomalies },
]);
const isEmpty = computed(() => props.training === 0 && props.deployments === 0 && props.anomalies === 0);
</script>

<template>
  <div class="activity-summary-panel">
    <dl class="activity-summary-list" role="list" aria-label="当前活动摘要">
      <div v-for="activity in activities" :key="activity.label" class="activity-summary-item" role="listitem">
        <dt>{{ activity.label }}</dt>
        <dd>{{ activity.value.toLocaleString("zh-CN") }}</dd>
      </div>
    </dl>
    <p v-if="isEmpty" data-testid="activity-summary-empty" class="activity-summary-empty" role="status">
      暂无活动数据
    </p>
  </div>
</template>

<style scoped>
.activity-summary-panel {
  min-width: 0;
}

.activity-summary-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  min-width: 0;
  margin: 0;
}

.activity-summary-item {
  display: grid;
  gap: 6px;
  min-width: 0;
  padding: 12px 16px;
  border-right: 1px solid #dde1e5;
}

.activity-summary-item:last-child {
  border-right: 0;
}

dt {
  color: #69727d;
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}

dd {
  margin: 0;
  color: #252a31;
  font-size: 22px;
  font-weight: 650;
  line-height: 28px;
  font-variant-numeric: tabular-nums;
}

.activity-summary-empty {
  margin: 8px 0 0;
  padding: 10px 12px;
  color: #78828e;
  background: #f5f6f7;
  font-size: 13px;
  line-height: 20px;
  text-align: center;
  overflow-wrap: anywhere;
}

@media (max-width: 520px) {
  .activity-summary-list {
    grid-template-columns: minmax(0, 1fr);
  }

  .activity-summary-item {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    border-right: 0;
    border-bottom: 1px solid #dde1e5;
  }

  .activity-summary-item:last-child {
    border-bottom: 0;
  }
}
</style>
