<template>
  <section class="model-space-view">
    <header class="page-header">
      <div>
        <p class="eyebrow">模型空间</p>
        <h1>模型资产管理</h1>
      </div>
      <el-button type="primary" :loading="loading" @click="loadModels">
        刷新
      </el-button>
    </header>

    <el-alert
      v-if="errorMessage"
      class="page-alert"
      type="warning"
      :title="errorMessage"
      show-icon
      :closable="false"
    />

    <el-row :gutter="16" class="summary-row">
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="summary-card">
          <span>基础模型</span>
          <strong>{{ baseModels.length }}</strong>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="summary-card">
          <span>训练模型</span>
          <strong>{{ trainedModels.length }}</strong>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="summary-card">
          <span>可用模型</span>
          <strong>{{ readyModelCount }}</strong>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="filter-panel">
      <el-form :inline="true" :model="filters" label-width="72px">
        <el-form-item label="关键词">
          <el-input
            v-model="filters.keyword"
            clearable
            placeholder="名称 / 版本 / 来源"
          />
        </el-form-item>
        <el-form-item label="任务">
          <el-select v-model="filters.task" clearable placeholder="全部任务">
            <el-option
              v-for="task in taskOptions"
              :key="task"
              :label="task"
              :value="task"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" clearable placeholder="全部状态">
            <el-option
              v-for="status in statusOptions"
              :key="status"
              :label="status"
              :value="status"
            />
          </el-select>
        </el-form-item>
      </el-form>
    </el-card>

    <el-tabs v-model="activeTab" class="model-tabs">
      <el-tab-pane label="基础模型" name="base">
        <el-table
          v-loading="loading"
          :data="filteredBaseModels"
          border
          empty-text="暂无基础模型"
        >
          <el-table-column prop="name" label="名称" min-width="180" />
          <el-table-column prop="version" label="版本" width="120" />
          <el-table-column prop="task" label="任务" width="120" />
          <el-table-column prop="scale" label="规模" width="100" />
          <el-table-column label="状态" width="130">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)">
                {{ row.status || "unknown" }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="source" label="模型源" min-width="160" />
          <el-table-column label="更新时间" width="180">
            <template #default="{ row }">
              {{ formatTime(row.updated_at || row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <el-button
                size="small"
                :loading="downloadingId === row.id"
                @click="downloadBase(row)"
              >
                下载
              </el-button>
              <el-button size="small" @click="loadModels">刷新</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="训练模型" name="trained">
        <el-table
          v-loading="loading"
          :data="filteredTrainedModels"
          border
          empty-text="暂无训练模型"
        >
          <el-table-column prop="name" label="名称" min-width="180" />
          <el-table-column prop="version" label="版本" width="120" />
          <el-table-column prop="task" label="任务" width="120" />
          <el-table-column label="指标" min-width="180">
            <template #default="{ row }">
              {{ formatMetrics(row.metrics) }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="130">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)">
                {{ row.status || "unknown" }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="产线" min-width="160">
            <template #default="{ row }">
              {{ row.pipeline_name || row.pipeline_id || "-" }}
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="180">
            <template #default="{ row }">
              {{ formatTime(row.created_at) }}
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="模型源概览" name="sources">
        <el-table :data="modelSources" border empty-text="暂无模型源">
          <el-table-column prop="source" label="模型源" min-width="180" />
          <el-table-column prop="total" label="模型数量" width="120" />
          <el-table-column prop="ready" label="可用" width="120" />
          <el-table-column prop="tasks" label="覆盖任务" min-width="220" />
          <el-table-column prop="latest" label="最近更新" width="180" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";

import { api } from "@/api/client";

type AnyRecord = Record<string, unknown>;

interface ModelRow extends AnyRecord {
  id: string;
  name?: string;
  version?: string;
  task?: string;
  scale?: string;
  status?: string;
  source?: string;
  created_at?: string;
  updated_at?: string;
  metrics?: unknown;
}

const activeTab = ref("base");
const loading = ref(false);
const downloadingId = ref("");
const errorMessage = ref("");
const baseModels = ref<ModelRow[]>([]);
const trainedModels = ref<ModelRow[]>([]);

const filters = reactive({
  keyword: "",
  task: "",
  status: "",
});

const taskOptions = ["detect", "segment", "semantic", "pose", "obb", "classify"];
const statusOptions = ["ready", "remote_available", "downloading", "training", "failed"];

const readyModelCount = computed(
  () =>
    [...baseModels.value, ...trainedModels.value].filter(
      (model) => model.status === "ready" || model.status === "success",
    ).length,
);

const filteredBaseModels = computed(() => filterModels(baseModels.value));
const filteredTrainedModels = computed(() => filterModels(trainedModels.value));

const modelSources = computed(() => {
  const groups = new Map<
    string,
    { source: string; total: number; ready: number; tasks: Set<string>; latest?: string }
  >();

  for (const model of baseModels.value) {
    const source = String(model.source || "internal");
    const current =
      groups.get(source) ||
      { source, total: 0, ready: 0, tasks: new Set<string>(), latest: undefined };
    current.total += 1;
    if (model.status === "ready") current.ready += 1;
    if (model.task) current.tasks.add(model.task);
    current.latest = pickLatest(current.latest, model.updated_at || model.created_at);
    groups.set(source, current);
  }

  return Array.from(groups.values()).map((group) => ({
    source: group.source,
    total: group.total,
    ready: group.ready,
    tasks: Array.from(group.tasks).join(", ") || "-",
    latest: formatTime(group.latest),
  }));
});

onMounted(() => {
  void loadModels();
});

async function loadModels() {
  loading.value = true;
  errorMessage.value = "";

  const [baseResult, trainedResult] = await Promise.allSettled([
    api.listBaseModels(),
    api.listTrainedModels(),
  ]);

  if (baseResult.status === "fulfilled") {
    baseModels.value = normalizeRows<ModelRow>(baseResult.value);
  } else {
    baseModels.value = [];
    errorMessage.value = "基础模型列表加载失败，已显示空表。";
  }

  if (trainedResult.status === "fulfilled") {
    trainedModels.value = normalizeRows<ModelRow>(trainedResult.value);
  } else {
    trainedModels.value = [];
    errorMessage.value = errorMessage.value
      ? `${errorMessage.value} 训练模型列表加载失败。`
      : "训练模型列表加载失败，已显示空表。";
  }

  loading.value = false;
}

async function downloadBase(row: ModelRow) {
  if (!row.id) return;
  downloadingId.value = row.id;
  try {
    await api.downloadBaseModel(row.id);
    ElMessage.success("已提交模型下载任务");
    await loadModels();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "模型下载失败"));
  } finally {
    downloadingId.value = "";
  }
}

function filterModels(rows: ModelRow[]) {
  const keyword = filters.keyword.trim().toLowerCase();
  return rows.filter((row) => {
    const text = [row.name, row.version, row.source, row.id]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    const keywordMatched = !keyword || text.includes(keyword);
    const taskMatched = !filters.task || row.task === filters.task;
    const statusMatched = !filters.status || row.status === filters.status;
    return keywordMatched && taskMatched && statusMatched;
  });
}

function normalizeRows<T extends AnyRecord>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (!payload || typeof payload !== "object") return [];
  const record = payload as AnyRecord;
  for (const key of ["items", "data", "results", "models"]) {
    const value = record[key];
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

function pickLatest(left?: string, right?: string) {
  if (!left) return right;
  if (!right) return left;
  return new Date(left).getTime() >= new Date(right).getTime() ? left : right;
}

function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatMetrics(metrics: unknown) {
  if (!metrics || typeof metrics !== "object") return "-";
  return Object.entries(metrics as Record<string, unknown>)
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" / ");
}

function statusTag(status?: string) {
  if (status === "ready" || status === "success") return "success";
  if (status === "failed") return "danger";
  if (status === "downloading" || status === "training") return "warning";
  return "info";
}

function getErrorMessage(error: unknown, fallback: string) {
  if (error instanceof Error) return error.message;
  return fallback;
}
</script>

<style scoped>
.model-space-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
}

.page-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.page-header h1 {
  font-size: 24px;
  font-weight: 650;
  line-height: 1.25;
  margin: 4px 0 0;
}

.eyebrow {
  color: #6b7280;
  font-size: 13px;
  margin: 0;
}

.page-alert {
  margin-bottom: 0;
}

.summary-row {
  row-gap: 16px;
}

.summary-card :deep(.el-card__body) {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.summary-card span {
  color: #6b7280;
  font-size: 14px;
}

.summary-card strong {
  color: #111827;
  font-size: 28px;
}

.filter-panel :deep(.el-card__body) {
  padding-bottom: 2px;
}

.model-tabs {
  min-width: 0;
}
</style>
