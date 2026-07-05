<template>
  <section class="data-preparation-view">
    <header class="page-header">
      <div>
        <p class="eyebrow">数据准备</p>
        <h1>数据集与样本管理</h1>
      </div>
      <el-button type="primary" :loading="loading" @click="loadDatasets">
        刷新
      </el-button>
    </header>

    <el-alert
      v-if="errorMessage"
      type="warning"
      :title="errorMessage"
      show-icon
      :closable="false"
    />

    <el-row :gutter="16">
      <el-col :xs="24" :lg="16">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>数据集列表</span>
              <el-input
                v-model="keyword"
                class="search-input"
                clearable
                placeholder="搜索名称 / 任务 / 状态"
              />
            </div>
          </template>

          <el-table
            v-loading="loading"
            :data="filteredDatasets"
            border
            empty-text="暂无数据集"
          >
            <el-table-column prop="name" label="名称" min-width="180" />
            <el-table-column prop="task" label="任务" width="120" />
            <el-table-column label="状态" width="130">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)">
                  {{ row.status || "unknown" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="样本" width="120">
              <template #default="{ row }">
                {{ numberValue(row.sample_count) }}
              </template>
            </el-table-column>
            <el-table-column label="标注" width="120">
              <template #default="{ row }">
                {{ numberValue(row.annotation_count) }}
              </template>
            </el-table-column>
            <el-table-column label="更新时间" width="180">
              <template #default="{ row }">
                {{ formatTime(row.updated_at || row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="300" fixed="right">
              <template #default="{ row }">
                <el-button
                  size="small"
                  :loading="actingId === `${row.id}:analyze`"
                  @click="analyze(row)"
                >
                  分析
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  plain
                  :loading="actingId === `${row.id}:validate`"
                  @click="validate(row)"
                >
                  校验
                </el-button>
                <el-button size="small" @click="openLabelProjects(row)">
                  标注
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="8">
        <div class="side-stack">
          <el-card shadow="never">
            <template #header>样本上传入口</template>
            <el-upload
              drag
              multiple
              :auto-upload="false"
              :file-list="uploadFiles"
              :on-change="handleUploadChange"
              :on-remove="handleUploadRemove"
            >
              <div class="upload-copy">
                <strong>拖入图片、视频帧或标注文件</strong>
                <span>当前仅作为上传入口占位，接入上传 API 后可直接提交。</span>
              </div>
            </el-upload>
            <div class="upload-footer">
              <span>{{ uploadFiles.length }} 个待处理文件</span>
              <el-button size="small" :disabled="uploadFiles.length === 0" @click="clearUploads">
                清空
              </el-button>
            </div>
          </el-card>

          <el-card shadow="never">
            <template #header>样本状态摘要</template>
            <div class="status-grid">
              <div>
                <span>数据集</span>
                <strong>{{ datasets.length }}</strong>
              </div>
              <div>
                <span>样本总数</span>
                <strong>{{ totalSamples }}</strong>
              </div>
              <div>
                <span>标注总数</span>
                <strong>{{ totalAnnotations }}</strong>
              </div>
              <div>
                <span>已验证</span>
                <strong>{{ validatedCount }}</strong>
              </div>
            </div>
          </el-card>

          <el-card shadow="never">
            <template #header>状态分布</template>
            <el-table :data="statusSummary" size="small" empty-text="暂无状态">
              <el-table-column prop="status" label="状态" />
              <el-table-column prop="count" label="数量" width="90" />
            </el-table>
          </el-card>
        </div>
      </el-col>
    </el-row>

    <el-drawer v-model="labelDrawer" title="Label Studio 标注" size="560px">
      <template v-if="selectedDataset">
        <div class="label-drawer-head">
          <div>
            <div class="drawer-title">{{ selectedDataset.name || selectedDataset.id }}</div>
            <div class="drawer-subtitle">{{ selectedDataset.task }} / {{ selectedDataset.status }}</div>
          </div>
          <el-button :loading="labelLoading" @click="loadLabelProjects">刷新</el-button>
        </div>

        <el-alert
          v-if="labelError"
          :title="labelError"
          type="warning"
          show-icon
          :closable="false"
          class="drawer-section"
        />

        <el-form :model="labelForm" label-width="120px" class="drawer-section">
          <el-form-item label="已有项目 ID">
            <el-input v-model="labelForm.external_project_id" placeholder="留空则新建 Label Studio 项目" />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              :loading="labelAction === 'create'"
              @click="createLabelProject"
            >
              创建/关联项目
            </el-button>
          </el-form-item>
        </el-form>

        <el-table
          v-loading="labelLoading"
          :data="labelProjects"
          row-key="id"
          empty-text="暂无 Label Studio 项目"
        >
          <el-table-column prop="external_project_id" label="外部项目" min-width="120" />
          <el-table-column prop="sync_status" label="同步状态" width="120">
            <template #default="{ row }">
              <el-tag :type="labelStatusType(row.sync_status)">{{ row.sync_status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="170">
            <template #default="{ row }">
              {{ formatTime(row.last_sync_at || row.updated_at) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <el-button
                size="small"
                :loading="labelAction === `${row.id}:sync`"
                @click="syncSamples(row.id)"
              >
                同步样本
              </el-button>
              <el-button
                size="small"
                type="primary"
                plain
                :loading="labelAction === `${row.id}:import`"
                @click="importAnnotations(row.id)"
              >
                导入标注
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import type { UploadFile, UploadFiles } from "element-plus";
import { ElMessage } from "element-plus";
import { computed, onMounted, ref } from "vue";

import { api, type LabelProjectRecord } from "@/api/client";

type AnyRecord = Record<string, unknown>;

interface DatasetRow extends AnyRecord {
  id: string;
  name?: string;
  task?: string;
  status?: string;
  sample_count?: number;
  annotation_count?: number;
  created_at?: string;
  updated_at?: string;
}

const loading = ref(false);
const actingId = ref("");
const errorMessage = ref("");
const keyword = ref("");
const datasets = ref<DatasetRow[]>([]);
const uploadFiles = ref<UploadFile[]>([]);
const labelDrawer = ref(false);
const labelLoading = ref(false);
const labelError = ref("");
const labelAction = ref("");
const selectedDataset = ref<DatasetRow | null>(null);
const labelProjects = ref<LabelProjectRecord[]>([]);
const labelForm = ref({ external_project_id: "" });

const filteredDatasets = computed(() => {
  const term = keyword.value.trim().toLowerCase();
  if (!term) return datasets.value;
  return datasets.value.filter((dataset) =>
    [dataset.name, dataset.task, dataset.status, dataset.id]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(term),
  );
});

const totalSamples = computed(() =>
  datasets.value.reduce((total, dataset) => total + numberValue(dataset.sample_count), 0),
);

const totalAnnotations = computed(() =>
  datasets.value.reduce((total, dataset) => total + numberValue(dataset.annotation_count), 0),
);

const validatedCount = computed(
  () => datasets.value.filter((dataset) => dataset.status === "validated").length,
);

const statusSummary = computed(() => {
  const groups = new Map<string, number>();
  for (const dataset of datasets.value) {
    const status = dataset.status || "unknown";
    groups.set(status, (groups.get(status) || 0) + 1);
  }
  return Array.from(groups.entries()).map(([status, count]) => ({ status, count }));
});

onMounted(() => {
  void loadDatasets();
});

async function loadDatasets() {
  loading.value = true;
  errorMessage.value = "";
  try {
    datasets.value = normalizeRows<DatasetRow>(await api.listDatasets());
  } catch (error) {
    datasets.value = [];
    errorMessage.value = getErrorMessage(error, "数据集加载失败，已显示空表。");
  } finally {
    loading.value = false;
  }
}

async function analyze(row: DatasetRow) {
  await runDatasetAction(row, "analyze", () => api.analyzeDataset(row.id), "已提交分析任务");
}

async function validate(row: DatasetRow) {
  await runDatasetAction(row, "validate", () => api.validateDataset(row.id), "已提交校验任务");
}

async function openLabelProjects(row: DatasetRow) {
  selectedDataset.value = row;
  labelDrawer.value = true;
  labelForm.value.external_project_id = "";
  await loadLabelProjects();
}

async function loadLabelProjects() {
  if (!selectedDataset.value) return;
  labelLoading.value = true;
  labelError.value = "";
  try {
    labelProjects.value = (await api.listLabelProjects(selectedDataset.value.id)).items;
  } catch (error) {
    labelProjects.value = [];
    labelError.value = getErrorMessage(error, "Label Studio 项目加载失败");
  } finally {
    labelLoading.value = false;
  }
}

async function createLabelProject() {
  if (!selectedDataset.value) return;
  labelAction.value = "create";
  labelError.value = "";
  try {
    const externalProjectId = labelForm.value.external_project_id.trim();
    await api.createLabelProject(
      selectedDataset.value.id,
      externalProjectId ? { external_project_id: externalProjectId } : {},
    );
    ElMessage.success("Label Studio 项目已就绪");
    labelForm.value.external_project_id = "";
    await loadLabelProjects();
  } catch (error) {
    labelError.value = getErrorMessage(error, "Label Studio 项目创建失败");
  } finally {
    labelAction.value = "";
  }
}

async function syncSamples(projectId: string) {
  await runLabelAction(projectId, "sync", () => api.syncLabelProjectSamples(projectId), "已提交样本同步任务");
}

async function importAnnotations(projectId: string) {
  await runLabelAction(projectId, "import", () => api.importLabelProjectAnnotations(projectId), "已提交标注导入任务");
}

async function runLabelAction(
  projectId: string,
  action: string,
  request: () => Promise<unknown>,
  successMessage: string,
) {
  labelAction.value = `${projectId}:${action}`;
  labelError.value = "";
  try {
    await request();
    ElMessage.success(successMessage);
    await loadLabelProjects();
  } catch (error) {
    labelError.value = getErrorMessage(error, "Label Studio 操作失败");
  } finally {
    labelAction.value = "";
  }
}

async function runDatasetAction(
  row: DatasetRow,
  action: string,
  request: () => Promise<unknown>,
  successMessage: string,
) {
  if (!row.id) return;
  actingId.value = `${row.id}:${action}`;
  try {
    await request();
    ElMessage.success(successMessage);
    await loadDatasets();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "操作失败"));
  } finally {
    actingId.value = "";
  }
}

function handleUploadChange(_file: UploadFile, files: UploadFiles) {
  uploadFiles.value = [...files];
}

function handleUploadRemove(_file: UploadFile, files: UploadFiles) {
  uploadFiles.value = [...files];
}

function clearUploads() {
  uploadFiles.value = [];
}

function normalizeRows<T extends AnyRecord>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (!payload || typeof payload !== "object") return [];
  const record = payload as AnyRecord;
  for (const key of ["items", "data", "results", "datasets"]) {
    const value = record[key];
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function statusTag(status?: string) {
  if (status === "validated" || status === "ready") return "success";
  if (status === "failed" || status === "invalid") return "danger";
  if (status === "analyzing" || status === "validating") return "warning";
  return "info";
}

function labelStatusType(status?: string) {
  if (status === "synced" || status === "imported") return "success";
  if (status === "failed") return "danger";
  if (status === "pending") return "warning";
  return "info";
}

function getErrorMessage(error: unknown, fallback: string) {
  if (error instanceof Error) return error.message;
  return fallback;
}
</script>

<style scoped>
.data-preparation-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
}

.page-header,
.card-header,
.upload-footer {
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

.search-input {
  max-width: 280px;
}

.side-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.upload-copy {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 24px 8px;
}

.upload-copy span,
.upload-footer span {
  color: #6b7280;
  font-size: 13px;
}

.upload-footer {
  margin-top: 12px;
}

.status-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.status-grid div {
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 12px;
}

.status-grid span {
  color: #6b7280;
  display: block;
  font-size: 13px;
  margin-bottom: 8px;
}

.status-grid strong {
  color: #111827;
  font-size: 24px;
}

.label-drawer-head {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.drawer-title {
  color: #111827;
  font-size: 18px;
  font-weight: 650;
}

.drawer-subtitle {
  color: #6b7280;
  font-size: 13px;
  margin-top: 4px;
}

.drawer-section {
  margin-top: 16px;
}
</style>
