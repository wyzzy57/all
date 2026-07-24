<template>
  <div>
    <div class="metric-row">
      <div class="metric">
        <div class="metric-label">任务总数</div>
        <div class="metric-value">{{ store.items.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">运行中</div>
        <div class="metric-value">{{ store.runningCount }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">失败</div>
        <div class="metric-value">{{ store.failedCount }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">最近刷新</div>
        <div class="metric-value small">{{ refreshedAt }}</div>
      </div>
    </div>

    <div class="panel">
      <div class="toolbar">
        <div class="toolbar-left">
          <h2 class="section-title">任务列表</h2>
          <el-alert v-if="store.error" :title="store.error" type="error" show-icon :closable="false" />
        </div>
        <div class="toolbar-right">
          <el-button :loading="store.loading" @click="refresh">刷新</el-button>
        </div>
      </div>
      <div class="task-table-scroll">
        <el-table v-loading="store.loading" :data="store.items" row-key="id" empty-text="暂无任务" class="task-table">
        <el-table-column prop="task_type" label="类型" min-width="210" />
        <el-table-column prop="status" label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="180">
          <template #default="{ row }">
            <el-progress :percentage="row.progress" :stroke-width="8" />
          </template>
        </el-table-column>
        <el-table-column prop="stage" label="阶段" width="150" />
        <el-table-column prop="resource_type" label="资源" width="150" />
        <el-table-column label="错误" min-width="220">
          <template #default="{ row }">
            <el-tooltip
              v-if="taskError(row) !== '-'"
              :content="taskError(row)"
              placement="top"
              effect="dark"
              popper-class="task-error-tooltip"
            >
              <span class="task-error" tabindex="0">{{ taskError(row) }}</span>
            </el-tooltip>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :disabled="!canCancel(row.status)" @click="store.cancel(row.id)">取消</el-button>
            <el-button size="small" type="danger" text :disabled="!canDelete(row.status)" @click="removeTask(row.id)">删除</el-button>
          </template>
        </el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { onMounted, ref } from "vue";

import { useTaskCenterStore } from "@/stores/taskCenter";
import type { TaskRecord } from "@/api/client";

const store = useTaskCenterStore();
const refreshedAt = ref("-");

function taskError(task: TaskRecord) {
  return task.error_message || task.error_code || "-";
}

function statusType(status: string) {
  if (status === "SUCCESS") return "success";
  if (status === "FAILED") return "danger";
  if (status === "RUNNING") return "warning";
  return "info";
}

function canCancel(status: string) {
  return status === "PENDING" || status === "QUEUED" || status === "RUNNING";
}

function canDelete(status: string) {
  return status === "SUCCESS" || status === "FAILED" || status === "CANCELED";
}

async function removeTask(id: string) {
  try {
    await ElMessageBox.confirm("删除后该任务将不再出现在任务中心。", "删除训练任务", {
      confirmButtonText: "删除",
      cancelButtonText: "取消",
      type: "warning",
    });
    await store.remove(id);
    ElMessage.success("训练任务已删除");
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof Error ? error.message : "训练任务删除失败");
    }
  }
}

async function refresh() {
  await store.refresh();
  refreshedAt.value = new Date().toLocaleTimeString();
}

onMounted(refresh);
</script>

<style scoped>
.small {
  font-size: 16px;
}

.task-error {
  display: block;
  max-width: 100%;
  overflow: hidden;
  color: #b42318;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: help;
}

.task-table-scroll {
  width: 100%;
  overflow-x: auto;
  scrollbar-gutter: stable;
}

.task-table {
  min-width: 1210px;
}
</style>
