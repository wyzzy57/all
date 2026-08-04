<script setup lang="ts">
import { ArrowRight } from "@element-plus/icons-vue";
import { ElIcon } from "element-plus";

defineProps<{
  title: string;
  meta?: string;
  description?: string;
  actionLabel?: string;
}>();

defineEmits<{
  action: [];
}>();
</script>

<template>
  <header class="dashboard-panel-heading">
    <div class="panel-heading-copy">
      <h2>{{ title }}</h2>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="meta || actionLabel" class="panel-heading-context">
      <span v-if="meta" class="panel-meta">{{ meta }}</span>
      <button
        v-if="actionLabel"
        type="button"
        class="panel-action"
        :aria-label="actionLabel"
        @click="$emit('action')"
      >
        <span>{{ actionLabel }}</span>
        <ElIcon aria-hidden="true"><ArrowRight /></ElIcon>
      </button>
    </div>
  </header>
</template>

<style scoped>
.dashboard-panel-heading {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 12px 20px;
  min-width: 0;
}

.panel-heading-copy {
  min-width: 0;
}

h2 {
  margin: 0;
  color: #252a31;
  font-size: 15px;
  font-weight: 650;
  line-height: 22px;
  overflow-wrap: anywhere;
}

p {
  margin: 4px 0 0;
  color: #5f6873;
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.panel-heading-context {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  min-width: 0;
}

.panel-meta {
  color: #5f6873;
  font-size: 12px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

.panel-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 44px;
  max-width: 100%;
  padding: 8px 0 8px 8px;
  color: #3568a8;
  background: transparent;
  border: 0;
  font: inherit;
  font-size: 13px;
  line-height: 20px;
  cursor: pointer;
}

.panel-action span {
  overflow-wrap: anywhere;
}

.panel-action .el-icon {
  flex: 0 0 auto;
  font-size: 14px;
}

.panel-action:hover {
  color: #204f88;
}

.panel-action:focus-visible {
  outline: 2px solid #3568a8;
  outline-offset: 2px;
}

@media (max-width: 600px) {
  .dashboard-panel-heading {
    grid-template-columns: minmax(0, 1fr);
    gap: 6px;
  }

  .panel-heading-context {
    justify-content: space-between;
    flex-wrap: wrap;
  }
}
</style>
