<template>
  <section class="data-preparation-view">
    <header class="page-title">
      <h1>数据准备</h1>
    </header>

    <el-alert
      v-if="errorMessage"
      type="warning"
      :title="errorMessage"
      show-icon
      :closable="false"
    />

    <div class="import-grid">
      <article class="import-card import-card-green">
        <div class="import-icon">↓</div>
        <h2>未标注数据导入</h2>
        <p>上传未标注的数据文件，支持图片、压缩包和数据文件夹</p>
        <el-button type="success" size="large" @click="startUnlabeledImport">
          导入数据
        </el-button>
      </article>

      <article class="import-card import-card-blue">
        <div class="import-icon">↓</div>
        <h2>已标注数据导入</h2>
        <p>上传已标注的数据文件，支持 COCO、YOLO 等常用数据集格式</p>
        <el-button type="primary" size="large" @click="startLabeledImport">
          导入数据
        </el-button>
      </article>

      <article class="import-card import-card-purple">
        <div class="import-icon">▶</div>
        <h2>视频文件导入</h2>
        <p>上传视频文件，选择模型和切帧策略进行处理</p>
        <el-button class="video-button" size="large" @click="startVideoImport">
          视频文件导入
        </el-button>
      </article>
    </div>

    <section class="upload-config">
      <el-form label-position="top" class="upload-form">
        <el-form-item label="上传方式">
          <el-segmented v-model="uploadMode" :options="uploadModeOptions" />
        </el-form-item>
        <template v-if="uploadMode === 'new'">
          <el-form-item label="数据集名称">
            <el-input v-model="newDataset.name" placeholder="选择文件夹后自动填入" />
          </el-form-item>
          <el-form-item label="任务类型">
            <el-select v-model="newDataset.task">
              <el-option label="图像检测" value="detect" />
              <el-option label="图像分割" value="segment" />
              <el-option label="语义分割" value="semantic" />
              <el-option label="姿态估计" value="pose" />
              <el-option label="旋转框检测" value="obb" />
              <el-option label="图像分类" value="classify" />
            </el-select>
          </el-form-item>
          <el-form-item label="类别">
            <el-input v-model="newDataset.classNames" placeholder="可留空；COCO/data.yaml 会自动读取" />
          </el-form-item>
        </template>
        <el-form-item v-else label="目标数据集">
          <el-select v-model="uploadDatasetId" filterable placeholder="选择要追加样本的数据集">
            <el-option
              v-for="dataset in datasets"
              :key="dataset.id"
              :label="dataset.name || dataset.id"
              :value="dataset.id"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <input
        ref="fileInputRef"
        class="visually-hidden"
        type="file"
        multiple
        accept=".jpg,.jpeg,.png,.bmp,.webp,.zip,.txt,.yaml,.yml,.json,image/*"
        @change="handleNativeFiles"
      />
      <input
        ref="folderInputRef"
        class="visually-hidden"
        type="file"
        multiple
        webkitdirectory
        @change="handleNativeFiles"
      />

      <div class="upload-strip">
        <div>
          <strong>{{ uploadFiles.length }} 个待上传文件</strong>
          <span>支持 COCO 文件夹、YOLO 文件夹、图片和 zip；文件夹上传会保留相对路径。</span>
        </div>
        <div class="upload-actions">
          <el-button @click="fileInputRef?.click()">选择文件</el-button>
          <el-button @click="folderInputRef?.click()">选择文件夹</el-button>
          <el-button :disabled="uploadFiles.length === 0" @click="clearUploads">清空</el-button>
          <el-button type="primary" :loading="uploading" :disabled="!canUpload" @click="uploadPendingFiles">
            上传
          </el-button>
        </div>
      </div>

      <el-table v-if="uploadFiles.length" :data="uploadPreview" size="small" class="upload-table" max-height="180">
        <el-table-column prop="name" label="文件" min-width="180" show-overflow-tooltip />
        <el-table-column prop="size" label="大小" width="100" />
      </el-table>
    </section>

    <div class="workspace-tabs">
      <div class="tab-list">
        <button :class="{ active: activeTab === 'prepare' }" @click="activeTab = 'prepare'">数据准备</button>
        <button :class="{ active: activeTab === 'datasets' }" @click="activeTab = 'datasets'">数据集</button>
      </div>
      <div class="toolbar">
        <el-button type="primary" :loading="syncingAll" @click="syncVisibleDatasets">
          数据同步
        </el-button>
        <el-input v-model="keyword" class="search-input" clearable placeholder="搜索" />
      </div>
    </div>

    <div v-loading="loading" class="dataset-grid">
      <button
        v-for="dataset in filteredDatasets"
        :key="dataset.id"
        class="dataset-card"
        type="button"
        @click="openDatasetLabelStudio(dataset)"
      >
        <div class="dataset-head">
          <strong>{{ dataset.name || dataset.id }}</strong>
          <span class="warning-mark">△</span>
        </div>
        <div class="chip-row">
          <span class="chip chip-success">✓ {{ datasetStatusText(dataset.status) }}</span>
          <span class="chip chip-success">▣ 导入</span>
          <span class="chip">{{ taskText(dataset.task) }}</span>
          <span v-if="primaryLabelProject(dataset)" class="chip">labelstudio导入</span>
        </div>
        <div class="dataset-meta">
          <span>{{ formatTime(dataset.updated_at || dataset.created_at) }}</span>
          <a href="#" @click.prevent.stop="openDatasetLabelStudio(dataset)">Label Studio</a>
        </div>
        <div class="dataset-stats">
          <span>样本 {{ numberValue(dataset.sample_count) }}</span>
          <span>标注 {{ numberValue(dataset.annotation_count) }}</span>
        </div>
      </button>

      <el-empty v-if="!loading && filteredDatasets.length === 0" description="暂无数据集" />
    </div>
  </section>
</template>

<script setup lang="ts">
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

const activeTab = ref("prepare");
const loading = ref(false);
const errorMessage = ref("");
const keyword = ref("");
const datasets = ref<DatasetRow[]>([]);
const labelProjectsByDataset = ref<Record<string, LabelProjectRecord[]>>({});
const uploadMode = ref("new");
const uploadModeOptions = [
  { label: "新建数据集", value: "new" },
  { label: "追加样本", value: "existing" },
];
const uploadDatasetId = ref("");
const newDataset = ref({ name: "", task: "detect", classNames: "" });
const uploadFiles = ref<File[]>([]);
const uploading = ref(false);
const syncingAll = ref(false);
const openingDatasetId = ref("");
const fileInputRef = ref<HTMLInputElement>();
const folderInputRef = ref<HTMLInputElement>();

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

const uploadPreview = computed(() =>
  uploadFiles.value.slice(0, 200).map((file) => ({
    name: file.webkitRelativePath || file.name,
    size: formatBytes(file.size),
  })),
);

const canUpload = computed(() => {
  if (uploadFiles.value.length === 0 || uploading.value) return false;
  if (uploadMode.value === "existing") return Boolean(uploadDatasetId.value);
  return Boolean(newDataset.value.name.trim());
});

onMounted(() => {
  void loadDatasets();
});

async function loadDatasets() {
  loading.value = true;
  errorMessage.value = "";
  try {
    datasets.value = normalizeRows<DatasetRow>(await api.listDatasets());
    await loadLabelProjectsForDatasets(datasets.value);
  } catch (error) {
    datasets.value = [];
    labelProjectsByDataset.value = {};
    errorMessage.value = getErrorMessage(error, "数据集加载失败，已显示空表。");
  } finally {
    loading.value = false;
  }
}

async function loadLabelProjectsForDatasets(rows: DatasetRow[]) {
  const entries = await Promise.all(
    rows.map(async (dataset) => {
      try {
        const response = await api.listLabelProjects(dataset.id);
        return [dataset.id, response.items] as const;
      } catch {
        return [dataset.id, []] as const;
      }
    }),
  );
  labelProjectsByDataset.value = Object.fromEntries(entries);
}

function startUnlabeledImport() {
  uploadMode.value = "new";
  fileInputRef.value?.click();
}

function startLabeledImport() {
  uploadMode.value = "new";
  folderInputRef.value?.click();
}

function startVideoImport() {
  ElMessage.warning("视频文件导入会在后续接入切帧任务；当前请先上传图片或数据集文件夹。");
}

async function openDatasetLabelStudio(row: DatasetRow) {
  if (openingDatasetId.value) return;
  openingDatasetId.value = row.id;
  try {
    const project = await ensureLabelProject(row);
    if (!project.project_url) {
      ElMessage.warning("Label Studio 项目已创建，但缺少可打开的项目地址。");
      return;
    }
    window.open(project.project_url, "_blank", "noopener,noreferrer");
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "Label Studio 项目打开失败"));
  } finally {
    openingDatasetId.value = "";
  }
}

async function ensureLabelProject(row: DatasetRow) {
  const existing = primaryLabelProject(row);
  if (existing) return existing;
  const project = await api.createLabelProject(row.id);
  labelProjectsByDataset.value = {
    ...labelProjectsByDataset.value,
    [row.id]: [project],
  };
  return project;
}

function primaryLabelProject(row: DatasetRow) {
  return labelProjectsByDataset.value[row.id]?.[0];
}

async function syncVisibleDatasets() {
  if (filteredDatasets.value.length === 0) return;
  syncingAll.value = true;
  let submitted = 0;
  try {
    for (const dataset of filteredDatasets.value) {
      const project = await ensureLabelProject(dataset);
      await api.syncLabelProjectSamples(project.id);
      submitted += 1;
    }
    ElMessage.success(`已提交 ${submitted} 个数据集的 Label Studio 同步任务`);
    await loadDatasets();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "数据同步失败"));
  } finally {
    syncingAll.value = false;
  }
}

function handleNativeFiles(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []).filter(isSupportedUploadFile);
  applyFolderDatasetName(files);
  const existing = new Set(uploadFiles.value.map(fileIdentity));
  const next = [...uploadFiles.value];
  for (const file of files) {
    const identity = fileIdentity(file);
    if (!existing.has(identity)) {
      existing.add(identity);
      next.push(file);
    }
  }
  uploadFiles.value = next;
  input.value = "";
}

async function uploadPendingFiles() {
  if (!canUpload.value) return;
  uploading.value = true;
  let created = 0;
  let duplicate = 0;
  let skipped = 0;
  try {
    const datasetId = await resolveUploadDatasetId();
    const result = shouldUseBatchUpload()
      ? await api.uploadDatasetBatch(datasetId, uploadFiles.value)
      : await api.uploadDatasetSample(datasetId, uploadFiles.value[0]);
    created += result.created_count;
    duplicate += result.duplicate_count;
    skipped += result.skipped_count;
    ElMessage.success(`上传完成：新增 ${created}，重复 ${duplicate}，跳过 ${skipped}`);
    clearUploads();
    await loadDatasets();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "样本上传失败"));
  } finally {
    uploading.value = false;
  }
}

function clearUploads() {
  uploadFiles.value = [];
}

function shouldUseBatchUpload() {
  return uploadFiles.value.length > 1 || uploadFiles.value.some((file) => Boolean(file.webkitRelativePath));
}

async function resolveUploadDatasetId() {
  if (uploadMode.value === "existing") return uploadDatasetId.value;
  const dataset = await api.createDataset({
    name: newDataset.value.name.trim(),
    task: newDataset.value.task,
    class_schema: { names: parseClassNames() },
    source: "upload",
  });
  uploadDatasetId.value = dataset.id;
  return dataset.id;
}

function parseClassNames() {
  return newDataset.value.classNames
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function applyFolderDatasetName(files: File[]) {
  if (uploadMode.value !== "new" || newDataset.value.name.trim()) return;
  const firstPath = files.find((file) => file.webkitRelativePath)?.webkitRelativePath;
  if (!firstPath) return;
  const folderName = firstPath.split("/").filter(Boolean)[0];
  if (folderName) {
    newDataset.value.name = folderName;
  }
}

function isSupportedUploadFile(file: File) {
  const name = file.name.toLowerCase();
  return [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".zip", ".txt", ".yaml", ".yml", ".json"].some((suffix) =>
    name.endsWith(suffix),
  );
}

function fileIdentity(file: File) {
  return `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`;
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
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

function datasetStatusText(status?: string) {
  if (status === "ready" || status === "validated") return "已完成";
  if (status === "failed" || status === "invalid") return "失败";
  if (status === "analyzing" || status === "validating") return "处理中";
  return status || "待处理";
}

function taskText(task?: string) {
  const map: Record<string, string> = {
    classify: "图像分类",
    detect: "目标检测",
    obb: "旋转框检测",
    pose: "姿态估计",
    segment: "图像分割",
    semantic: "语义分割",
  };
  return task ? map[task] || task : "未设置";
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
  gap: 24px;
  padding: 28px 20px 32px;
}

.page-title h1 {
  color: #07111f;
  font-size: 28px;
  font-weight: 700;
  line-height: 1.25;
  margin: 0;
}

.import-grid {
  display: grid;
  gap: 18px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.import-card {
  background: #ffffff;
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
  display: flex;
  flex-direction: column;
  min-height: 252px;
  padding: 26px 25px 22px;
}

.import-icon {
  align-items: center;
  border-radius: 12px;
  color: #ffffff;
  display: flex;
  font-size: 28px;
  font-weight: 700;
  height: 75px;
  justify-content: center;
  margin-bottom: 18px;
  width: 75px;
}

.import-card h2 {
  color: #07111f;
  font-size: 25px;
  font-weight: 700;
  line-height: 1.25;
  margin: 0 0 12px;
}

.import-card p {
  color: #738096;
  flex: 1;
  font-size: 19px;
  line-height: 1.45;
  margin: 0 0 20px;
}

.import-card :deep(.el-button) {
  border-radius: 4px;
  font-size: 18px;
  font-weight: 700;
  height: 56px;
  width: 100%;
}

.import-card-green .import-icon {
  background: #16a34a;
  box-shadow: 0 0 0 20px #dcfce7;
}

.import-card-blue .import-icon {
  background: #2f7cf6;
  box-shadow: 0 0 0 20px #dbeafe;
}

.import-card-purple .import-icon {
  background: #9333ea;
  box-shadow: 0 0 0 20px #f3e8ff;
}

.video-button {
  --el-button-bg-color: #9333ea;
  --el-button-border-color: #9333ea;
  --el-button-hover-bg-color: #8b28df;
  --el-button-hover-border-color: #8b28df;
  --el-button-text-color: #ffffff;
}

.upload-config {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
}

.upload-form {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(4, minmax(180px, 1fr));
}

.upload-form :deep(.el-form-item) {
  margin-bottom: 0;
}

.upload-strip {
  align-items: center;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 4px;
  display: flex;
  gap: 16px;
  justify-content: space-between;
  padding: 14px;
}

.upload-strip span {
  color: #64748b;
  display: block;
  font-size: 13px;
  margin-top: 5px;
}

.upload-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.upload-table {
  margin-top: 2px;
}

.visually-hidden {
  height: 1px;
  opacity: 0;
  overflow: hidden;
  position: absolute;
  width: 1px;
}

.workspace-tabs {
  align-items: center;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  justify-content: space-between;
  margin-top: 2px;
  min-height: 76px;
}

.tab-list {
  align-self: stretch;
  display: flex;
  gap: 34px;
}

.tab-list button {
  background: transparent;
  border: 0;
  color: #1f2937;
  cursor: pointer;
  font-size: 20px;
  font-weight: 500;
  padding: 0;
  position: relative;
}

.tab-list button.active {
  color: #2563eb;
  font-weight: 700;
}

.tab-list button.active::after {
  background: #2563eb;
  bottom: 0;
  content: "";
  height: 3px;
  left: 6px;
  position: absolute;
  right: 6px;
}

.toolbar {
  align-items: center;
  display: flex;
  gap: 12px;
}

.toolbar :deep(.el-button) {
  border-radius: 4px;
  font-size: 16px;
  font-weight: 700;
  height: 50px;
  min-width: 120px;
}

.search-input {
  width: 330px;
}

.search-input :deep(.el-input__wrapper) {
  background: #f3f4f6;
  border-radius: 4px;
  box-shadow: none;
  height: 50px;
}

.dataset-grid {
  display: grid;
  gap: 20px;
  grid-template-columns: repeat(4, minmax(250px, 1fr));
  min-height: 220px;
}

.dataset-card {
  background: #ffffff;
  border: 1px solid #dce3ee;
  border-radius: 4px;
  cursor: pointer;
  min-height: 180px;
  padding: 26px 25px;
  text-align: left;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
}

.dataset-card:hover {
  border-color: #93c5fd;
  box-shadow: 0 14px 28px rgba(15, 23, 42, 0.08);
  transform: translateY(-1px);
}

.dataset-head {
  align-items: center;
  display: flex;
  gap: 5px;
  margin-bottom: 16px;
}

.dataset-head strong {
  color: #07111f;
  font-size: 21px;
  line-height: 1.25;
  word-break: break-word;
}

.warning-mark {
  color: #60a5fa;
  font-size: 18px;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.chip {
  align-items: center;
  background: #f1f5f9;
  border-radius: 999px;
  color: #0f172a;
  display: inline-flex;
  font-size: 16px;
  line-height: 1.2;
  min-height: 40px;
  padding: 8px 14px;
}

.chip-success {
  background: #dcfce7;
  color: #16a34a;
}

.dataset-meta {
  align-items: center;
  color: #58677a;
  display: flex;
  flex-wrap: wrap;
  font-size: 16px;
  gap: 10px;
}

.dataset-meta a {
  color: #2563eb;
  text-decoration: none;
}

.dataset-stats {
  color: #64748b;
  display: flex;
  gap: 14px;
  font-size: 14px;
  margin-top: 12px;
}

@media (max-width: 1280px) {
  .import-grid,
  .dataset-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 980px) {
  .import-grid,
  .dataset-grid,
  .upload-form {
    grid-template-columns: 1fr;
  }

  .workspace-tabs,
  .upload-strip {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar {
    width: 100%;
  }

  .search-input {
    flex: 1;
    width: auto;
  }
}
</style>
