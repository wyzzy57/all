<template>
  <section class="data-preparation-view">
    <section v-if="datasetDetail" class="dataset-detail-page">
      <button class="back-link" type="button" @click="datasetDetail = null">
        <ArrowLeft />
        返回数据集列表
      </button>

      <header class="detail-hero">
        <div>
          <h1>{{ datasetDetail.name || datasetDetail.id }}</h1>
          <p>{{ formatTime(datasetDetail.created_at) }}</p>
          <span>{{ taskText(datasetDetail.task) }}数据集，包含 {{ numberValue(datasetDetail.sample_count) }} 个文件。</span>
        </div>
        <div class="detail-actions">
          <el-button type="primary" @click="openDatasetProcessing(datasetDetail)">
            数据处理
          </el-button>
          <el-button plain @click="confirmDeleteDataset(datasetDetail)">
            <Delete />
            删除
          </el-button>
          <el-button plain>
            <Edit />
            编辑
          </el-button>
          <el-button plain>
            <Star />
            收藏
          </el-button>
        </div>
      </header>

      <div class="detail-tabs">
        <button class="active" type="button">数据集详情</button>
      </div>

      <section class="dataset-intro">
        <span>添加数据集介绍</span>
      </section>

      <section class="file-list-panel">
        <header>
          <h2>文件列表</h2>
        </header>
        <div class="file-list-row">
          <span>output_231</span>
          <a
            :href="api.datasetExportUrl(datasetDetail.id)"
            :download="`${datasetDetail.name || datasetDetail.id}.zip`"
            :data-testid="`download-dataset-${datasetDetail.id}`"
          >
            下载
          </a>
        </div>
      </section>
    </section>

    <template v-else>
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

      <section v-if="operationState.visible" class="operation-panel">
        <div class="operation-head">
          <div>
            <strong>{{ operationState.title }}</strong>
            <span>{{ operationState.detail }}</span>
          </div>
          <el-tag :type="operationTagType">{{ operationStatusText }}</el-tag>
        </div>
        <el-progress :percentage="operationPercent" :status="operationProgressStatus" />
        <el-alert
          v-if="operationState.error"
          class="operation-alert"
          type="error"
          :title="operationState.error"
          show-icon
          :closable="false"
        />
        <ul v-if="operationState.failures.length" class="failure-list">
          <li v-for="failure in operationState.failures" :key="failure.name">
            <strong>{{ failure.name }}</strong>
            <span>{{ failure.message }}</span>
          </li>
        </ul>
      </section>

      <div class="import-grid">
        <article class="import-card import-card-green">
          <div class="import-icon">
            <Upload />
          </div>
          <h2>未标注数据导入</h2>
          <p>上传未标注的数据文件，支持图片、压缩包和数据文件夹</p>
          <el-button type="success" size="large" data-testid="import-unlabeled" @click="openImportDialog('unlabeled')">
            导入数据
          </el-button>
        </article>

        <article class="import-card import-card-blue">
          <div class="import-icon">
            <FolderOpened />
          </div>
          <h2>已标注数据导入</h2>
          <p>上传已标注的数据文件，支持 COCO、YOLO 等常用数据集格式</p>
          <el-button type="primary" size="large" data-testid="import-labeled" @click="openImportDialog('labeled')">
            导入数据
          </el-button>
        </article>

        <article class="import-card import-card-purple">
          <div class="import-icon">
            <VideoCamera />
          </div>
          <h2>视频文件导入</h2>
          <p>上传视频文件，选择模型和切帧策略进行处理</p>
          <el-button class="video-button" size="large" data-testid="import-video" @click="openImportDialog('video')">
            视频文件导入
          </el-button>
        </article>
      </div>

      <div class="workspace-tabs">
        <div class="tab-list">
          <button :class="{ active: activeTab === 'prepare' }" type="button" @click="activeTab = 'prepare'">数据准备</button>
          <button
            :class="{ active: activeTab === 'datasets' }"
            type="button"
            data-testid="dataset-tab"
            @click="activeTab = 'datasets'"
          >
            数据集
          </button>
        </div>
        <div class="toolbar">
          <el-button v-if="activeTab === 'prepare'" type="primary" :loading="syncingAll" @click="syncVisibleDatasets">
            数据同步
          </el-button>
          <el-input v-model="keyword" class="search-input" clearable placeholder="搜索">
            <template #suffix>
              <Search />
            </template>
          </el-input>
        </div>
      </div>

      <div v-loading="loading" class="dataset-grid" :class="{ 'dataset-grid-compact': activeTab === 'datasets' }">
        <article
          v-for="dataset in filteredDatasets"
          :key="dataset.id"
          class="dataset-card"
          :class="{ 'prepare-card': activeTab === 'prepare', 'library-card': activeTab === 'datasets' }"
          role="button"
          tabindex="0"
          @click="handleDatasetCardClick(dataset)"
          @keydown.enter.prevent="handleDatasetCardClick(dataset)"
          @keydown.space.prevent="handleDatasetCardClick(dataset)"
        >
          <div class="dataset-actions" @click.stop>
            <button
              class="dataset-more-button"
              type="button"
              :aria-label="`更多操作：${dataset.name || dataset.id}`"
              :data-testid="`dataset-more-${dataset.id}`"
              @click="toggleDatasetMenu(dataset.id)"
            >
              <MoreFilled />
            </button>
            <div v-if="activeDatasetMenuId === dataset.id" class="dataset-action-menu">
              <button v-if="activeTab === 'datasets'" type="button">编辑</button>
              <button v-if="activeTab === 'datasets'" type="button">公开配置</button>
              <button
                v-if="activeTab === 'prepare'"
                type="button"
                :disabled="convertingDatasetId === dataset.id"
                @click="convertToDataset(dataset)"
              >
                {{ convertingDatasetId === dataset.id ? "转换中" : "转为数据集" }}
              </button>
              <button
                class="danger"
                type="button"
                :data-testid="`delete-dataset-${dataset.id}`"
                :disabled="deletingDatasetId === dataset.id"
                @click="confirmDeleteDataset(dataset)"
              >
                删除
              </button>
            </div>
          </div>

          <template v-if="activeTab === 'prepare'">
            <div class="prepare-card-head">
              <span class="status-dot"></span>
              <strong>{{ dataset.name || dataset.id }}</strong>
            </div>
            <div class="chip-row">
              <span class="chip chip-success">✓ {{ datasetStatusText(dataset.status) }}</span>
              <span class="chip chip-success">▣ {{ importSourceText(dataset) }}</span>
              <span class="chip">{{ taskText(dataset.task) }}</span>
            </div>
            <div class="dataset-meta">
              <span>{{ formatTime(dataset.updated_at || dataset.created_at) }}</span>
              <a href="#" @click.prevent.stop="openDatasetLabelStudio(dataset)">
                <Link />
                Label Studio
              </a>
            </div>
            <div class="dataset-stats">
              <span>样本 {{ numberValue(dataset.sample_count) }}</span>
              <span>标注 {{ numberValue(dataset.annotation_count) }}</span>
            </div>
          </template>

          <template v-else>
            <header class="library-card-head">
              <strong>{{ dataset.name || dataset.id }}</strong>
              <span>label</span>
            </header>
            <div class="library-tags">
              <span>{{ primaryLabelProject(dataset) ? "labelstudio导入" : importSourceText(dataset) }}</span>
            </div>
            <p>{{ taskText(dataset.task) }}数据集，共 {{ numberValue(dataset.sample_count) }} 个文件</p>
            <time>{{ formatTime(dataset.updated_at || dataset.created_at) }}</time>
            <div class="dataset-stats">
              <span>标注 {{ numberValue(dataset.annotation_count) }}</span>
              <button
                v-if="canValidateDataset(dataset)"
                class="dataset-inline-action"
                type="button"
                :data-testid="`validate-dataset-${dataset.id}`"
                :disabled="validatingDatasetId === dataset.id"
                @click.prevent.stop="validateDataset(dataset)"
              >
                {{ validateDatasetButtonText(dataset) }}
              </button>
              <button
                class="dataset-inline-action"
                type="button"
                :data-testid="`process-dataset-${dataset.id}`"
                @click.prevent.stop="openDatasetProcessing(dataset)"
              >
                数据处理
              </button>
            </div>
          </template>
        </article>

        <el-empty v-if="!loading && filteredDatasets.length === 0" description="暂无数据集" />
      </div>
    </template>

    <el-dialog v-model="importDialogVisible" title="新增对应的数据集" width="760px" class="import-dialog">
      <h2 class="import-dialog-title">新增对应的数据集</h2>
      <div class="import-modal-tabs">
        <button :class="{ active: importMode === 'unlabeled' }" type="button" @click="importMode = 'unlabeled'">未标注数据导入</button>
        <button :class="{ active: importMode === 'labeled' }" type="button" @click="importMode = 'labeled'">已标注数据导入</button>
        <button :class="{ active: importMode === 'video' }" type="button" @click="importMode = 'video'">视频文件导入</button>
      </div>

      <input
        ref="fileInputRef"
        class="visually-hidden"
        type="file"
        multiple
        :accept="acceptedUploadTypes"
        @change="handleNativeFiles"
      />
      <button class="upload-dropzone" type="button" @click="fileInputRef?.click()">
        <Upload />
        <strong>点击或拖拽文件到此处上传</strong>
        <span>{{ uploadHintText }}</span>
      </button>

      <section class="modal-section">
        <header>
          <strong>数据处理规则</strong>
        </header>
        <div class="modal-form-grid">
          <label>
            <span><i>*</i> 数据集名称</span>
            <input v-model="newDataset.name" placeholder="请输入数据集名称" />
          </label>
          <label>
            <span><i>*</i> 标签</span>
            <input v-model="newDataset.classNames" placeholder="请输入标签，多个标签用逗号分隔" />
          </label>
        </div>
      </section>

      <section class="modal-section">
        <button class="advanced-toggle" type="button" @click="advancedOpen = !advancedOpen">
          高级设置
          <span>{{ advancedOpen ? "⌃" : "⌄" }}</span>
        </button>
        <div v-if="advancedOpen" class="advanced-grid">
          <label>
            <span>模型选择</span>
            <small>请选择适配的模型</small>
            <select v-model="videoSettings.model">
              <option value="">请选择选择模型</option>
              <option>YOLO26-n</option>
              <option>YOLO26-s</option>
              <option>YOLO26-m</option>
            </select>
          </label>
          <label>
            <span>识别类型</span>
            <small>选择识别类型，包含目标检测、人员</small>
            <select v-model="videoSettings.recognition">
              <option value="">请选择识别类型</option>
              <option>目标检测</option>
              <option>图像分割</option>
              <option>图像分类</option>
            </select>
          </label>
          <label>
            <span>视频分辨率</span>
            <small>切图分辨率</small>
            <select v-model="videoSettings.resolution">
              <option value="">请选择视频分辨率</option>
              <option>原始分辨率</option>
              <option>1920x1080</option>
              <option>1280x720</option>
            </select>
          </label>
          <label>
            <span>切帧频率</span>
            <small>建议间隔5s</small>
            <input v-model.number="videoSettings.frameInterval" type="number" min="1" />
          </label>
          <label>
            <span>最小置信度</span>
            <small>建议0.6往上</small>
            <input v-model.number="videoSettings.confidence" type="number" min="0" max="1" step="0.01" />
          </label>
          <div class="checkbox-cluster">
            <span>增强规则</span>
            <div class="checkbox-row">
              <label><input v-model="videoSettings.augment.all" type="checkbox" @change="toggleAllAugmentRules" /> 全选</label>
              <label><input v-model="videoSettings.augment.horizontalFlip" type="checkbox" @change="syncAugmentAllState" /> 水平翻转</label>
              <label><input v-model="videoSettings.augment.verticalFlip" type="checkbox" @change="syncAugmentAllState" /> 垂直翻转</label>
              <label><input v-model="videoSettings.augment.rotate" type="checkbox" @change="syncAugmentAllState" /> 旋转</label>
              <label><input v-model="videoSettings.augment.translate" type="checkbox" @change="syncAugmentAllState" /> 平移</label>
              <label><input v-model="videoSettings.augment.scale" type="checkbox" @change="syncAugmentAllState" /> 缩放</label>
              <label><input v-model="videoSettings.augment.noise" type="checkbox" @change="syncAugmentAllState" /> 噪声</label>
              <label><input v-model="videoSettings.augment.blur" type="checkbox" @change="syncAugmentAllState" /> 模糊</label>
            </div>
          </div>
          <div class="checkbox-cluster">
            <span>清洗规则</span>
            <div class="checkbox-row">
              <label><input v-model="videoSettings.clean.all" type="checkbox" @change="toggleAllCleanRules" /> 全选</label>
              <label><input v-model="videoSettings.clean.exactDuplicate" type="checkbox" @change="syncCleanAllState" /> 完全重复</label>
              <label><input v-model="videoSettings.clean.nearDuplicate" type="checkbox" @change="syncCleanAllState" /> 接近重复</label>
              <label><input v-model="videoSettings.clean.blur" type="checkbox" @change="syncCleanAllState" /> 模糊</label>
              <label><input v-model="videoSettings.clean.lowInformation" type="checkbox" @change="syncCleanAllState" /> 低信息密度</label>
              <label><input v-model="videoSettings.clean.tooDark" type="checkbox" @change="syncCleanAllState" /> 过暗</label>
              <label><input v-model="videoSettings.clean.tooBright" type="checkbox" @change="syncCleanAllState" /> 过亮</label>
              <label><input v-model="videoSettings.clean.aspectRatio" type="checkbox" @change="syncCleanAllState" /> 长宽比异常</label>
              <label><input v-model="videoSettings.clean.size" type="checkbox" @change="syncCleanAllState" /> 大小异常</label>
              <label><input v-model="videoSettings.clean.gray" type="checkbox" @change="syncCleanAllState" /> 灰色</label>
            </div>
          </div>
        </div>
      </section>

      <section v-if="uploadFiles.length" class="upload-preview-list">
        <strong>{{ uploadFiles.length }} 个待上传文件</strong>
        <ul>
          <li v-for="file in uploadPreview" :key="file.name">
            <span>{{ file.name }}</span>
            <em>{{ file.size }}</em>
          </li>
        </ul>
      </section>

      <footer class="dialog-footer">
        <el-button type="primary" :loading="uploading" :disabled="!canUpload" @click="uploadPendingFiles">
          {{ importMode === "video" ? "上传并处理" : "上传" }}
        </el-button>
      </footer>
    </el-dialog>

    <el-drawer
      v-model="processingDrawerVisible"
      class="dataset-processing-drawer"
      size="72%"
      :with-header="false"
      @closed="resetProcessingPanel"
    >
      <section v-if="selectedDataset" class="processing-panel">
        <header class="processing-header">
          <div>
            <p>数据处理</p>
            <h2>{{ selectedDataset.name || selectedDataset.id }}</h2>
            <span>{{ taskText(selectedDataset.task) }} · 样本 {{ numberValue(selectedDataset.sample_count) }} · 标注 {{ numberValue(selectedDataset.annotation_count) }}</span>
          </div>
          <div class="processing-actions">
            <el-button @click="loadProcessingData(selectedDataset)">刷新分析</el-button>
            <el-button type="primary" @click="openDatasetLabelStudio(selectedDataset)">去标注</el-button>
          </div>
        </header>

        <el-alert
          v-if="processingError"
          type="warning"
          :title="processingError"
          show-icon
          :closable="false"
        />

        <section class="split-panel">
          <div class="split-inputs">
            <label>
              <span>训练集</span>
              <input v-model.number="splitForm.train" type="number" min="0" max="100" />
            </label>
            <label>
              <span>验证集</span>
              <input v-model.number="splitForm.val" type="number" min="0" max="100" />
            </label>
            <label>
              <span>测试集</span>
              <input v-model.number="splitForm.test" type="number" min="0" max="100" />
            </label>
            <el-button
              type="primary"
              :loading="splittingDataset"
              :disabled="splitRatioTotal !== 100"
              @click="applySplitRatio"
            >
              应用切分
            </el-button>
          </div>
          <span :class="{ invalid: splitRatioTotal !== 100 }">比例合计 {{ splitRatioTotal }}%</span>
        </section>

        <section v-loading="processingLoading" class="analysis-summary">
          <div class="summary-card">
            <strong>{{ validationPassedText }}</strong>
            <span>数据校验状态</span>
          </div>
          <div class="summary-card">
            <strong>{{ splitSummary.train.count }} 个样本</strong>
            <span>训练集，占比 {{ splitSummary.train.percent }}%</span>
          </div>
          <div class="summary-card">
            <strong>{{ splitSummary.val.count }} 个样本</strong>
            <span>验证集，占比 {{ splitSummary.val.percent }}%</span>
          </div>
          <div class="summary-card">
            <strong>{{ classCount }} 个</strong>
            <span>类别数量</span>
          </div>
        </section>

        <div class="processing-tabs">
          <button :class="{ active: processingTab === 'train' }" type="button" @click="processingTab = 'train'">训练集</button>
          <button :class="{ active: processingTab === 'val' }" type="button" @click="processingTab = 'val'">验证集</button>
          <button :class="{ active: processingTab === 'test' }" type="button" @click="processingTab = 'test'">测试集</button>
          <button :class="{ active: processingTab === 'classes' }" type="button" @click="processingTab = 'classes'">类别分布图</button>
        </div>

        <section v-if="processingTab !== 'classes'" class="sample-visualizer">
          <div class="sample-stage">
            <img
              v-if="activePreviewSample"
              :src="sampleImageUrl(activePreviewSample)"
              :alt="activePreviewSample.id"
            />
            <el-empty v-else description="暂无样本" />
          </div>
          <div class="sample-strip">
            <button
              v-for="sample in visibleSamples"
              :key="sample.id"
              type="button"
              :class="{ active: activePreviewSample?.id === sample.id }"
              @click="activeSampleId = sample.id"
            >
              <img :src="sampleImageUrl(sample)" :alt="sample.id" />
            </button>
          </div>
        </section>

        <section v-else class="class-chart">
          <div v-for="item in classDistributionItems" :key="item.name" class="class-row">
            <span>{{ item.name }}</span>
            <div>
              <i :style="{ width: `${item.percent}%` }"></i>
            </div>
            <strong>{{ item.count }}</strong>
          </div>
          <el-empty v-if="classDistributionItems.length === 0" description="暂无类别统计" />
        </section>
      </section>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import {
  ArrowLeft,
  Delete,
  Edit,
  FolderOpened,
  Link,
  MoreFilled,
  Search,
  Star,
  Upload,
  VideoCamera,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

import { api, type DatasetSampleRecord, type LabelProjectRecord } from "@/api/client";

type AnyRecord = Record<string, unknown>;
type ImportMode = "unlabeled" | "labeled" | "video";
type ProcessingTab = "train" | "val" | "test" | "classes";

interface DatasetRow extends AnyRecord {
  id: string;
  name?: string;
  task?: string;
  status?: string;
  source?: string | null;
  storage_uri?: string | null;
  sample_count?: number;
  annotation_count?: number;
  created_at?: string;
  updated_at?: string;
}

type DatasetAnalysis = {
  sample_count?: number;
  annotation_count?: number;
  class_distribution?: Record<string, number>;
  split_distribution?: Record<string, number>;
  image_size_distribution?: {
    total_with_dimensions?: number;
    by_size?: Record<string, number>;
  };
  empty_annotation_ratio?: number;
  invalid_samples?: string[];
};

type AugmentRuleKey = "horizontalFlip" | "verticalFlip" | "rotate" | "translate" | "scale" | "noise" | "blur";
type CleanRuleKey =
  | "exactDuplicate"
  | "nearDuplicate"
  | "blur"
  | "lowInformation"
  | "tooDark"
  | "tooBright"
  | "aspectRatio"
  | "size"
  | "gray";

const activeTab = ref<"prepare" | "datasets">("prepare");
const loading = ref(false);
const errorMessage = ref("");
const keyword = ref("");
const datasets = ref<DatasetRow[]>([]);
const datasetDetail = ref<DatasetRow | null>(null);
const labelProjectsByDataset = ref<Record<string, LabelProjectRecord[]>>({});
const importDialogVisible = ref(false);
const importMode = ref<ImportMode>("unlabeled");
const advancedOpen = ref(false);
const uploadMode = ref("new");
const uploadDatasetId = ref("");
const newDataset = ref({ name: "", task: "detect", classNames: "" });
const uploadFiles = ref<File[]>([]);
const uploading = ref(false);
const syncingAll = ref(false);
const deletingDatasetId = ref("");
const validatingDatasetId = ref("");
const convertingDatasetId = ref("");
const activeDatasetMenuId = ref("");
const processingDrawerVisible = ref(false);
const processingLoading = ref(false);
const splittingDataset = ref(false);
const processingError = ref("");
const selectedDataset = ref<DatasetRow | null>(null);
const analysisResult = ref<DatasetAnalysis | null>(null);
const processingTab = ref<ProcessingTab>("train");
const activeSampleId = ref("");
const splitForm = ref({ train: 80, val: 20, test: 0 });
const samplesBySplit = ref<Record<"train" | "val" | "test", DatasetSampleRecord[]>>({
  train: [],
  val: [],
  test: [],
});
const fileInputRef = ref<HTMLInputElement>();
const videoSettings = ref({
  model: "",
  recognition: "",
  resolution: "",
  frameInterval: 5,
  confidence: 0.6,
  augment: {
    all: false,
    horizontalFlip: false,
    verticalFlip: false,
    rotate: false,
    translate: false,
    scale: false,
    noise: false,
    blur: false,
  },
  clean: {
    all: false,
    exactDuplicate: false,
    nearDuplicate: false,
    blur: false,
    lowInformation: false,
    tooDark: false,
    tooBright: false,
    aspectRatio: false,
    size: false,
    gray: false,
  },
});
const operationState = ref({
  visible: false,
  title: "",
  detail: "",
  current: 0,
  total: 0,
  status: "idle" as "idle" | "running" | "success" | "failed",
  error: "",
  failures: [] as Array<{ name: string; message: string }>,
});

const filteredDatasets = computed(() => {
  const rows = datasets.value.filter((dataset) =>
    activeTab.value === "prepare" ? isPreparationRecord(dataset) : dataset.status !== "preparing",
  );
  const term = keyword.value.trim().toLowerCase();
  if (!term) return rows;
  return rows.filter((dataset) =>
    [dataset.name, dataset.task, dataset.status, dataset.id]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(term),
  );
});

function isPreparationRecord(dataset: DatasetRow) {
  if (dataset.status === "preparing") return true;
  const storageUri = dataset.storage_uri || "";
  if (storageUri.startsWith("preparation://")) return false;
  return dataset.source !== "label_studio";
}

const uploadPreview = computed(() =>
  uploadFiles.value.slice(0, 200).map((file) => ({
    name: file.webkitRelativePath || file.name,
    size: formatBytes(file.size),
  })),
);

const acceptedUploadTypes = computed(() =>
  importMode.value === "video"
    ? ".mp4,.avi,.mov,.mkv,video/*"
    : ".jpg,.jpeg,.png,.bmp,.webp,.zip,.txt,.yaml,.yml,.json,image/*",
);

const uploadHintText = computed(() => {
  if (importMode.value === "video") return "支持 MP4、AVI、MOV、MKV 视频文件";
  if (importMode.value === "labeled") return "支持 COCO 文件夹、YOLO 文件夹、图片和 zip";
  return "支持图片、压缩包和数据文件夹";
});

const canUpload = computed(() => {
  if (uploadFiles.value.length === 0 || uploading.value) return false;
  if (uploadMode.value === "existing") return Boolean(uploadDatasetId.value);
  return Boolean(newDataset.value.name.trim() && newDataset.value.classNames.trim());
});

const operationPercent = computed(() => {
  if (!operationState.value.total) return 0;
  return Math.round((operationState.value.current / operationState.value.total) * 100);
});

const operationTagType = computed(() => {
  if (operationState.value.status === "success") return "success";
  if (operationState.value.status === "failed") return "danger";
  if (operationState.value.status === "running") return "warning";
  return "info";
});

const operationProgressStatus = computed(() => {
  if (operationState.value.status === "success") return "success";
  if (operationState.value.status === "failed") return "exception";
  return undefined;
});

const operationStatusText = computed(() => {
  if (operationState.value.status === "success") return "已完成";
  if (operationState.value.status === "failed") return "失败";
  if (operationState.value.status === "running") return "处理中";
  return "待处理";
});

const splitRatioTotal = computed(() => splitForm.value.train + splitForm.value.val + splitForm.value.test);

const splitSummary = computed(() => {
  const distribution = analysisResult.value?.split_distribution ?? {};
  const total = Math.max(analysisResult.value?.sample_count ?? selectedDataset.value?.sample_count ?? 0, 0);
  return {
    train: splitStats(distribution.train ?? 0, total),
    val: splitStats(distribution.val ?? 0, total),
    test: splitStats(distribution.test ?? 0, total),
  };
});

const classDistributionItems = computed(() => {
  const distribution = analysisResult.value?.class_distribution ?? {};
  const max = Math.max(...Object.values(distribution), 0);
  return Object.entries(distribution)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => ({
      name,
      count,
      percent: max > 0 ? Math.max(6, Math.round((count / max) * 100)) : 0,
    }));
});

const classCount = computed(() => classDistributionItems.value.length);
const validationPassedText = computed(() => (selectedDataset.value?.status === "validated" ? "数据校验通过" : "待校验"));
const visibleSamples = computed(() => (processingTab.value === "classes" ? [] : samplesBySplit.value[processingTab.value] ?? []));
const activePreviewSample = computed(() => {
  const samples = visibleSamples.value;
  return samples.find((sample) => sample.id === activeSampleId.value) ?? samples[0];
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
    errorMessage.value = getErrorMessage(error, "数据集加载失败");
  } finally {
    loading.value = false;
  }
}

async function loadLabelProjectsForDatasets(rows: DatasetRow[]) {
  const entries = await Promise.all(
    rows.map(async (dataset) => {
      try {
        const payload = await api.listLabelProjects(dataset.id);
        return [dataset.id, normalizeRows<LabelProjectRecord>(payload)] as const;
      } catch {
        return [dataset.id, []] as const;
      }
    }),
  );
  labelProjectsByDataset.value = Object.fromEntries(entries);
}

function openImportDialog(mode: ImportMode) {
  importMode.value = mode;
  advancedOpen.value = mode === "video";
  importDialogVisible.value = true;
}

function handleDatasetCardClick(dataset: DatasetRow) {
  activeDatasetMenuId.value = "";
  if (activeTab.value === "prepare") {
    void openDatasetLabelStudio(dataset);
    return;
  }
  datasetDetail.value = dataset;
}

async function convertToDataset(dataset: DatasetRow) {
  activeDatasetMenuId.value = "";
  convertingDatasetId.value = dataset.id;
  startOperation("转为数据集", 3, "正在从 Label Studio 导入标注");
  try {
    const project = await ensureLabelProject(dataset);
    const importTask = await api.importLabelProjectAnnotations(project.id);
    updateOperation(1, "正在读取 Label Studio 标注");
    const completedTask = await waitForTask(importTask.id);
    if (completedTask.status !== "SUCCESS") {
      throw new Error(completedTask.error_message || "Label Studio 标注导入失败");
    }
    updateOperation(2, "正在筛选有标注的样本");
    const promoted = await api.promoteDataset(dataset.id);
    updateOperation(3, "训练数据集已生成");
    finishOperation(`已生成 ${promoted.sample_count} 个有标注样本的数据集`);
    await loadDatasets();
    activeTab.value = "datasets";
    datasetDetail.value = promoted;
    ElMessage.success("已将有标注的样本转为数据集");
  } catch (error) {
    failOperation(getErrorMessage(error, "转为数据集失败"));
    ElMessage.error(getErrorMessage(error, "没有可转换的有效标注"));
  } finally {
    convertingDatasetId.value = "";
  }
}

async function waitForTask(taskId: string) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const task = await api.getTask(taskId);
    if (["SUCCESS", "FAILED", "CANCELED"].includes(task.status)) return task;
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  throw new Error("标注导入超时，请稍后重试");
}

async function openDatasetProcessing(row: DatasetRow) {
  selectedDataset.value = row;
  processingDrawerVisible.value = true;
  processingTab.value = "train";
  activeSampleId.value = "";
  await loadProcessingData(row);
}

async function loadProcessingData(row: DatasetRow) {
  processingLoading.value = true;
  processingError.value = "";
  try {
    const [analysisTask, trainSamples, valSamples, testSamples] = await Promise.all([
      api.analyzeDataset(row.id),
      api.listDatasetSamples(row.id, { split: "train", limit: 60 }),
      api.listDatasetSamples(row.id, { split: "val", limit: 60 }),
      api.listDatasetSamples(row.id, { split: "test", limit: 60 }),
    ]);
    analysisResult.value = readAnalysisResult(analysisTask.payload);
    samplesBySplit.value = {
      train: normalizeRows<DatasetSampleRecord>(trainSamples),
      val: normalizeRows<DatasetSampleRecord>(valSamples),
      test: normalizeRows<DatasetSampleRecord>(testSamples),
    };
    const first = samplesBySplit.value.train[0] ?? samplesBySplit.value.val[0] ?? samplesBySplit.value.test[0];
    activeSampleId.value = first?.id ?? "";
  } catch (error) {
    analysisResult.value = null;
    samplesBySplit.value = { train: [], val: [], test: [] };
    processingError.value = getErrorMessage(error, "数据分析加载失败");
  } finally {
    processingLoading.value = false;
  }
}

async function applySplitRatio() {
  if (!selectedDataset.value || splitRatioTotal.value !== 100) return;
  splittingDataset.value = true;
  try {
    await api.assignDatasetSplitRatio(selectedDataset.value.id, {
      train_ratio: splitForm.value.train,
      val_ratio: splitForm.value.val,
      test_ratio: splitForm.value.test,
    });
    ElMessage.success("数据切分已应用");
    await loadProcessingData(selectedDataset.value);
    await loadDatasets();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "数据切分失败"));
  } finally {
    splittingDataset.value = false;
  }
}

function resetProcessingPanel() {
  selectedDataset.value = null;
  analysisResult.value = null;
  processingError.value = "";
  activeSampleId.value = "";
  samplesBySplit.value = { train: [], val: [], test: [] };
}

function sampleImageUrl(sample: DatasetSampleRecord) {
  return api.datasetSampleContentUrl(sample.dataset_id, sample.id);
}

function primaryLabelProject(dataset: DatasetRow) {
  return labelProjectsByDataset.value[dataset.id]?.[0];
}

async function openDatasetLabelStudio(row: DatasetRow) {
  try {
    const project = await ensureLabelProject(row);
    if (!project.project_url) {
      ElMessage.warning("Label Studio 项目已创建，但缺少可打开的项目地址。");
      return;
    }
    window.open(project.project_url, "_blank", "noopener,noreferrer");
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "Label Studio 项目打开失败"));
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

function toggleDatasetMenu(datasetId: string) {
  activeDatasetMenuId.value = activeDatasetMenuId.value === datasetId ? "" : datasetId;
}

async function confirmDeleteDataset(row: DatasetRow) {
  try {
    await ElMessageBox.confirm(`确认删除数据集「${row.name || row.id}」？删除后不可恢复。`, "删除数据集", {
      confirmButtonText: "删除",
      cancelButtonText: "取消",
      type: "warning",
    });
    deletingDatasetId.value = row.id;
    await api.deleteDataset(row.id);
    ElMessage.success("数据集已删除");
    activeDatasetMenuId.value = "";
    if (datasetDetail.value?.id === row.id) datasetDetail.value = null;
    await loadDatasets();
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(getErrorMessage(error, "数据集删除失败"));
    }
  } finally {
    deletingDatasetId.value = "";
  }
}

function canValidateDataset(dataset: DatasetRow) {
  return numberValue(dataset.sample_count) > 0;
}

function validateDatasetButtonText(dataset: DatasetRow) {
  if (validatingDatasetId.value === dataset.id) return "检验中";
  return dataset.status === "validated" ? "重新检验" : "检验";
}

async function validateDataset(row: DatasetRow) {
  validatingDatasetId.value = row.id;
  try {
    const task = await api.validateDataset(row.id);
    if (task.status === "SUCCESS") {
      ElMessage.success("数据集检验通过，已可用于训练");
    } else {
      ElMessage.warning(task.error_message || "数据集检验未通过，请检查样本和标注");
    }
    await loadDatasets();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "数据集检验失败"));
  } finally {
    validatingDatasetId.value = "";
  }
}

async function syncVisibleDatasets() {
  if (filteredDatasets.value.length === 0) return;
  syncingAll.value = true;
  let submitted = 0;
  const failures: Array<{ name: string; message: string }> = [];
  startOperation("数据同步", filteredDatasets.value.length, "正在提交 Label Studio 同步任务");
  try {
    for (const dataset of filteredDatasets.value) {
      updateOperation(submitted + failures.length, `正在同步 ${dataset.name || dataset.id}`);
      try {
        const project = await ensureLabelProject(dataset);
        await api.syncLabelProjectSamples(project.id);
        submitted += 1;
      } catch (error) {
        failures.push({
          name: dataset.name || dataset.id,
          message: getErrorMessage(error, "同步失败"),
        });
      }
    }
    updateOperation(filteredDatasets.value.length, `已提交 ${submitted} 个同步任务，失败 ${failures.length} 个`);
    operationState.value.failures = failures;
    if (failures.length) {
      operationState.value.status = "failed";
      operationState.value.error = "部分数据集同步失败，请查看下方明细。";
      ElMessage.warning(`已提交 ${submitted} 个同步任务，失败 ${failures.length} 个`);
    } else {
      finishOperation(`已提交 ${submitted} 个数据集的 Label Studio 同步任务`);
      ElMessage.success(`已提交 ${submitted} 个数据集的 Label Studio 同步任务`);
    }
    await loadDatasets();
  } catch (error) {
    failOperation(getErrorMessage(error, "数据同步失败"));
    ElMessage.error(getErrorMessage(error, "数据同步失败"));
  } finally {
    syncingAll.value = false;
  }
}

function handleNativeFiles(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []).filter(isSupportedUploadFile);
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

const augmentRuleKeys: AugmentRuleKey[] = ["horizontalFlip", "verticalFlip", "rotate", "translate", "scale", "noise", "blur"];
const cleanRuleKeys: CleanRuleKey[] = [
  "exactDuplicate",
  "nearDuplicate",
  "blur",
  "lowInformation",
  "tooDark",
  "tooBright",
  "aspectRatio",
  "size",
  "gray",
];

function toggleAllAugmentRules() {
  setAugmentRules(videoSettings.value.augment.all);
}

function syncAugmentAllState() {
  videoSettings.value.augment.all = augmentRuleKeys.every((key) => videoSettings.value.augment[key]);
}

function setAugmentRules(checked: boolean) {
  for (const key of augmentRuleKeys) {
    videoSettings.value.augment[key] = checked;
  }
}

function toggleAllCleanRules() {
  setCleanRules(videoSettings.value.clean.all);
}

function syncCleanAllState() {
  videoSettings.value.clean.all = cleanRuleKeys.every((key) => videoSettings.value.clean[key]);
}

function setCleanRules(checked: boolean) {
  for (const key of cleanRuleKeys) {
    videoSettings.value.clean[key] = checked;
  }
}

function selectedAugmentRules() {
  return Object.fromEntries(augmentRuleKeys.map((key) => [key, videoSettings.value.augment[key]]));
}

function selectedCleanRules() {
  return Object.fromEntries(cleanRuleKeys.map((key) => [key, videoSettings.value.clean[key]]));
}

function hasProcessingRulesSelected() {
  return (
    augmentRuleKeys.some((key) => videoSettings.value.augment[key]) ||
    cleanRuleKeys.some((key) => videoSettings.value.clean[key])
  );
}

function readTaskResult(payload?: Record<string, unknown>) {
  const result = payload?.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return {};
  return result as Record<string, unknown>;
}

async function uploadPendingFiles() {
  if (!canUpload.value) return;
  uploading.value = true;
  let created = 0;
  let duplicate = 0;
  let skipped = 0;
  startOperation("数据导入", 3, "正在准备数据集");
  try {
    updateOperation(1, "正在准备数据集");
    const datasetId = await resolveUploadDatasetId();
    updateOperation(2, `正在上传 ${uploadFiles.value.length} 个文件`);
    const result = shouldUseBatchUpload()
      ? await api.uploadDatasetBatch(datasetId, uploadFiles.value)
      : await api.uploadDatasetSample(datasetId, uploadFiles.value[0]);
    created += result.created_count;
    duplicate += result.duplicate_count;
    skipped += result.skipped_count;
    if (hasProcessingRulesSelected()) {
      updateOperation(3, "???????????");
      const task = await api.processDataset(datasetId, {
        augment: selectedAugmentRules(),
        clean: selectedCleanRules(),
        max_samples: 500,
      });
      const processResult = readTaskResult(task.payload);
      const augmented = Number(processResult.augmented_count ?? 0);
      const cleaningIssues = Number(processResult.cleaning_issue_count ?? 0);
      finishOperation(`??????????? ${augmented}??????? ${cleaningIssues}`);
      ElMessage.success(`??????????? ${augmented}??????? ${cleaningIssues}`);
    } else {
      finishOperation(`??????? ${created}??? ${duplicate}??? ${skipped}`);
      ElMessage.success(`??????? ${created}??? ${duplicate}??? ${skipped}`);
    }
    uploadFiles.value = [];
    importDialogVisible.value = false;
    await loadDatasets();
  } catch (error) {
    failOperation(getErrorMessage(error, "样本上传失败"));
    ElMessage.error(getErrorMessage(error, "样本上传失败"));
  } finally {
    uploading.value = false;
  }
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
    source: importMode.value === "video" ? "video" : "upload",
    preparation: true,
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

function isSupportedUploadFile(file: File) {
  const name = file.name.toLowerCase();
  const common = [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".zip", ".txt", ".yaml", ".yml", ".json"];
  const videos = [".mp4", ".avi", ".mov", ".mkv"];
  return [...common, ...videos].some((suffix) => name.endsWith(suffix));
}

function fileIdentity(file: File) {
  return `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`;
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function startOperation(title: string, total: number, detail: string) {
  operationState.value = {
    visible: true,
    title,
    detail,
    current: 0,
    total,
    status: "running",
    error: "",
    failures: [],
  };
}

function updateOperation(current: number, detail: string) {
  operationState.value.current = Math.min(current, operationState.value.total);
  operationState.value.detail = detail;
}

function finishOperation(detail: string) {
  operationState.value.current = operationState.value.total;
  operationState.value.detail = detail;
  operationState.value.status = "success";
  operationState.value.error = "";
}

function failOperation(message: string) {
  operationState.value.status = "failed";
  operationState.value.error = message;
  operationState.value.detail = "处理未完成";
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

function importSourceText(dataset: DatasetRow) {
  if (dataset.source === "video") return "视频导入";
  if (numberValue(dataset.annotation_count) > 0) return "已标注导入";
  return "未标注导入";
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

function splitStats(count: number, total: number) {
  return {
    count,
    percent: total > 0 ? Number(((count / total) * 100).toFixed(2)) : 0,
  };
}

function readAnalysisResult(payload?: Record<string, unknown>): DatasetAnalysis {
  const result = payload?.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return {};
  return result as DatasetAnalysis;
}

function getErrorMessage(error: unknown, fallback: string) {
  if (error instanceof Error) return error.message;
  return fallback;
}
</script>

<style scoped>
.data-preparation-view {
  container: data-preparation / inline-size;
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
  padding: 0 0 8px;
}

.page-title h1,
.detail-hero h1 {
  color: #07111f;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.25;
  margin: 0;
}

.import-grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.import-card {
  background: #ffffff;
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.045);
  display: flex;
  flex-direction: column;
  min-height: 178px;
  padding: 18px 18px 16px;
}

.import-icon {
  align-items: center;
  border-radius: 12px;
  color: #ffffff;
  display: flex;
  height: 42px;
  justify-content: center;
  margin-bottom: 14px;
  width: 42px;
}

.import-icon svg {
  height: 20px;
  width: 20px;
}

.import-card h2 {
  color: #07111f;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.25;
  margin: 0 0 8px;
}

.import-card p {
  color: #738096;
  flex: 1;
  font-size: 14px;
  line-height: 1.5;
  margin: 0 0 14px;
}

.import-card :deep(.el-button) {
  border-radius: 4px;
  font-size: 14px;
  font-weight: 700;
  height: 38px;
  width: 100%;
}

.import-card-green .import-icon {
  background: #16a34a;
  box-shadow: 0 0 0 9px #dcfce7;
}

.import-card-blue .import-icon {
  background: #2f7cf6;
  box-shadow: 0 0 0 9px #dbeafe;
}

.import-card-purple .import-icon {
  background: #9333ea;
  box-shadow: 0 0 0 9px #f3e8ff;
}

.video-button {
  --el-button-bg-color: #9333ea;
  --el-button-border-color: #9333ea;
  --el-button-hover-bg-color: #8b28df;
  --el-button-hover-border-color: #8b28df;
  --el-button-text-color: #ffffff;
}

.operation-panel {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
}

.operation-head {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
}

.operation-head strong {
  color: #0f172a;
  display: block;
  font-size: 16px;
  margin-bottom: 4px;
}

.operation-head span,
.failure-list span {
  color: #64748b;
  font-size: 13px;
}

.operation-alert {
  margin-top: 2px;
}

.failure-list {
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}

.failure-list li {
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 4px;
  color: #7f1d1d;
  display: grid;
  gap: 4px;
  padding: 10px 12px;
}

.workspace-tabs {
  align-items: center;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  justify-content: space-between;
  margin-top: 0;
  min-height: 54px;
}

.tab-list {
  align-self: stretch;
  display: flex;
  gap: 26px;
}

.tab-list button {
  background: transparent;
  border: 0;
  color: #1f2937;
  cursor: pointer;
  font-size: 16px;
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
  font-size: 14px;
  font-weight: 700;
  height: 36px;
  min-width: 96px;
}

.search-input {
  width: 260px;
}

.search-input :deep(.el-input__wrapper) {
  background: #f3f4f6;
  border-radius: 4px;
  box-shadow: none;
  height: 36px;
}

.search-input svg {
  color: #4b5563;
  height: 18px;
  width: 18px;
}

.dataset-grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  min-height: 220px;
}

.dataset-grid-compact {
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
}

.dataset-card {
  background: #ffffff;
  border: 1px solid #dce3ee;
  border-radius: 4px;
  cursor: pointer;
  min-height: 138px;
  padding: 18px 18px 16px;
  position: relative;
  text-align: left;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
}

.dataset-card:hover {
  border-color: #93c5fd;
  box-shadow: 0 14px 28px rgba(15, 23, 42, 0.08);
  transform: translateY(-1px);
}

.dataset-card:hover .dataset-more-button,
.dataset-card:focus-within .dataset-more-button {
  opacity: 1;
  pointer-events: auto;
}

.library-card {
  min-height: 122px;
  padding: 16px 16px 14px;
}

.dataset-actions {
  position: absolute;
  right: 10px;
  top: 10px;
  z-index: 2;
}

.dataset-more-button {
  align-items: center;
  background: #f8fafc;
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  color: #475569;
  display: inline-flex;
  height: 28px;
  justify-content: center;
  opacity: 0;
  padding: 0;
  pointer-events: none;
  transition: opacity 0.15s ease, border-color 0.15s ease, color 0.15s ease;
  width: 28px;
}

.dataset-more-button:hover,
.dataset-more-button:focus-visible {
  border-color: #93c5fd;
  color: #2563eb;
  outline: none;
}

.dataset-more-button svg {
  height: 18px;
  width: 18px;
}

.dataset-action-menu {
  background: #ffffff;
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.14);
  min-width: 128px;
  padding: 6px;
  position: absolute;
  right: 0;
  top: 38px;
}

.dataset-action-menu button {
  background: transparent;
  border: 0;
  border-radius: 4px;
  color: #334155;
  cursor: pointer;
  display: block;
  font-size: 14px;
  line-height: 1.2;
  padding: 9px 10px;
  text-align: left;
  width: 100%;
}

.dataset-action-menu button:hover,
.dataset-action-menu button:focus-visible {
  background: #eff6ff;
  outline: none;
}

.dataset-action-menu .danger {
  color: #dc2626;
}

.dataset-action-menu .danger:hover,
.dataset-action-menu .danger:focus-visible {
  background: #fef2f2;
}

.prepare-card-head {
  align-items: center;
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.prepare-card-head strong,
.library-card-head strong {
  color: #07111f;
  font-size: 16px;
  line-height: 1.25;
  word-break: break-word;
}

.status-dot {
  background: #22c55e;
  border-radius: 999px;
  height: 8px;
  width: 8px;
}

.warning-mark {
  color: #60a5fa;
  font-size: 14px;
}

.chip-row,
.library-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.chip,
.library-tags span {
  align-items: center;
  background: #f1f5f9;
  border-radius: 999px;
  color: #0f172a;
  display: inline-flex;
  font-size: 12px;
  line-height: 1.2;
  min-height: 26px;
  padding: 5px 9px;
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
  font-size: 13px;
  gap: 8px;
}

.dataset-meta a {
  align-items: center;
  color: #2563eb;
  display: inline-flex;
  gap: 5px;
  text-decoration: none;
}

.dataset-meta svg {
  height: 15px;
  width: 15px;
}

.dataset-stats {
  align-items: center;
  color: #64748b;
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 10px;
  margin-top: 10px;
}

.dataset-inline-action {
  background: transparent;
  border: 0;
  color: #2563eb;
  cursor: pointer;
  font-size: 12px;
  line-height: 1.2;
  padding: 0;
}

.dataset-inline-action:hover,
.dataset-inline-action:focus-visible {
  text-decoration: underline;
}

.dataset-inline-action:disabled {
  color: #94a3b8;
  cursor: wait;
  text-decoration: none;
}

.library-card-head {
  align-items: center;
  display: flex;
  gap: 8px;
  justify-content: space-between;
  margin-bottom: 10px;
  padding-right: 26px;
}

.library-card-head span {
  background: #eef2ff;
  border-radius: 4px;
  color: #2563eb;
  font-size: 12px;
  padding: 3px 7px;
}

.library-tags span {
  background: #f1f5f9;
  color: #64748b;
  font-size: 12px;
  min-height: 26px;
  padding: 5px 9px;
}

.library-card p {
  color: #64748b;
  font-size: 13px;
  line-height: 1.5;
  margin: 0 0 10px;
}

.library-card time {
  color: #94a3b8;
  font-size: 13px;
}

.dataset-detail-page {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.back-link {
  align-items: center;
  background: transparent;
  border: 0;
  color: #2563eb;
  cursor: pointer;
  display: inline-flex;
  font-size: 15px;
  gap: 6px;
  padding: 0;
  width: fit-content;
}

.back-link svg,
.detail-actions svg {
  height: 16px;
  width: 16px;
}

.detail-hero {
  align-items: flex-start;
  display: flex;
  justify-content: space-between;
  gap: 24px;
}

.detail-hero p {
  color: #64748b;
  font-size: 14px;
  margin: 12px 0 8px;
}

.detail-hero span {
  color: #475569;
  font-size: 15px;
}

.detail-actions {
  align-items: center;
  display: flex;
  gap: 10px;
}

.detail-actions :deep(.el-button) {
  align-items: center;
  border-radius: 4px;
  display: inline-flex;
  gap: 6px;
}

.detail-tabs {
  border-bottom: 1px solid #e5e7eb;
}

.detail-tabs button {
  background: transparent;
  border: 0;
  border-bottom: 3px solid #2563eb;
  color: #2563eb;
  cursor: pointer;
  font-size: 17px;
  font-weight: 700;
  padding: 0 4px 14px;
}

.dataset-intro {
  align-items: center;
  background: #f7f8fb;
  border: 1px dashed #dbe3ef;
  color: #a3acba;
  display: flex;
  font-size: 22px;
  height: 260px;
  justify-content: center;
}

.file-list-panel {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
}

.file-list-panel header {
  border-bottom: 1px solid #e5e7eb;
  padding: 16px 18px;
}

.file-list-panel h2 {
  color: #0f172a;
  font-size: 18px;
  margin: 0;
}

.file-list-row {
  align-items: center;
  display: flex;
  justify-content: space-between;
  padding: 16px 18px;
}

.file-list-row span {
  color: #334155;
  font-size: 15px;
}

.file-list-row a {
  color: #2563eb;
  text-decoration: none;
}

.visually-hidden {
  height: 1px;
  opacity: 0;
  overflow: hidden;
  position: absolute;
  width: 1px;
}

.import-dialog :deep(.el-dialog__body) {
  padding-top: 6px;
}

.import-dialog-title {
  color: #07111f;
  font-size: 20px;
  font-weight: 700;
  margin: 0 0 18px;
}

.import-modal-tabs {
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  gap: 28px;
  margin-bottom: 22px;
}

.import-modal-tabs button {
  background: transparent;
  border: 0;
  color: #475569;
  cursor: pointer;
  font-size: 16px;
  padding: 0 0 12px;
  position: relative;
}

.import-modal-tabs button.active {
  color: #2563eb;
  font-weight: 700;
}

.import-modal-tabs button.active::after {
  background: #2563eb;
  bottom: -1px;
  content: "";
  height: 2px;
  left: 0;
  position: absolute;
  right: 0;
}

.upload-dropzone {
  align-items: center;
  background: #f8fafc;
  border: 1px dashed #bcd0ea;
  border-radius: 4px;
  color: #64748b;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 142px;
  padding: 24px;
  width: 100%;
}

.upload-dropzone svg {
  color: #2563eb;
  height: 34px;
  width: 34px;
}

.upload-dropzone strong {
  color: #334155;
  font-size: 16px;
}

.modal-section {
  border-bottom: 1px solid #eef2f7;
  padding: 20px 0;
}

.modal-section header {
  color: #0f172a;
  margin-bottom: 14px;
}

.modal-form-grid {
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.advanced-grid {
  display: grid;
  gap: 16px 10px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 18px;
}

.modal-form-grid label,
.advanced-grid label,
.checkbox-cluster {
  display: grid;
  gap: 8px;
}

.modal-form-grid span,
.advanced-grid span,
.checkbox-cluster > span {
  color: #344054;
  font-size: 14px;
}

.modal-form-grid i {
  color: #f04438;
  font-style: normal;
}

.advanced-grid small {
  color: #98a2b3;
  font-size: 12px;
  line-height: 1.2;
  margin-top: -4px;
}

.modal-form-grid input,
.modal-form-grid select,
.advanced-grid input,
.advanced-grid select {
  border: 1px solid #d0d5dd;
  border-radius: 4px;
  color: #0f172a;
  height: 38px;
  padding: 0 10px;
}

.advanced-toggle {
  align-items: center;
  background: transparent;
  border: 0;
  color: #07111f;
  cursor: pointer;
  display: flex;
  font-size: 16px;
  font-weight: 700;
  justify-content: space-between;
  padding: 0;
  width: 100%;
}

.checkbox-cluster label {
  align-items: center;
  color: #475569;
  display: flex;
  font-size: 14px;
  gap: 8px;
}

.checkbox-cluster {
  grid-column: 1 / -1;
}

.checkbox-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
}

.checkbox-row label {
  display: inline-flex;
  min-width: auto;
}

.upload-preview-list {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  margin-top: 18px;
  padding: 14px;
}

.upload-preview-list strong {
  color: #0f172a;
  display: block;
  margin-bottom: 10px;
}

.upload-preview-list ul {
  display: grid;
  gap: 6px;
  list-style: none;
  margin: 0;
  max-height: 160px;
  overflow: auto;
  padding: 0;
}

.upload-preview-list li {
  align-items: center;
  color: #475569;
  display: flex;
  font-size: 13px;
  justify-content: space-between;
}

.upload-preview-list em {
  color: #94a3b8;
  font-style: normal;
}

.dialog-footer {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  padding-top: 20px;
}

.processing-panel {
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: 26px;
}

.processing-header {
  align-items: center;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  gap: 18px;
  justify-content: space-between;
  padding-bottom: 18px;
}

.processing-header p {
  color: #2563eb;
  font-size: 14px;
  font-weight: 700;
  margin: 0 0 6px;
}

.processing-header h2 {
  color: #0f172a;
  font-size: 24px;
  line-height: 1.25;
  margin: 0 0 7px;
}

.processing-header span {
  color: #64748b;
  font-size: 14px;
}

.processing-actions,
.split-inputs {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.split-panel {
  align-items: center;
  background: #f8fafc;
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  display: flex;
  gap: 14px;
  justify-content: space-between;
  padding: 14px;
}

.split-panel label {
  align-items: center;
  display: flex;
  gap: 8px;
}

.split-panel label span,
.split-panel > span {
  color: #475569;
  font-size: 14px;
}

.split-panel input {
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  color: #0f172a;
  height: 34px;
  padding: 0 8px;
  width: 74px;
}

.split-panel .invalid {
  color: #dc2626;
}

.analysis-summary {
  border: 1px solid #dbe3ef;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.summary-card {
  display: grid;
  gap: 8px;
  padding: 18px;
}

.summary-card + .summary-card {
  border-left: 1px solid #e5e7eb;
}

.summary-card strong {
  color: #0f172a;
  font-size: 16px;
}

.summary-card span {
  color: #64748b;
  font-size: 14px;
}

.processing-tabs {
  display: flex;
}

.processing-tabs button {
  background: #ffffff;
  border: 1px solid #dbe3ef;
  color: #334155;
  cursor: pointer;
  font-size: 15px;
  min-width: 96px;
  padding: 11px 16px;
}

.processing-tabs button + button {
  border-left: 0;
}

.processing-tabs button.active {
  background: #eff6ff;
  color: #2563eb;
  font-weight: 700;
}

.sample-visualizer {
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  display: grid;
  gap: 16px;
  padding: 16px 18px;
}

.sample-stage {
  align-items: center;
  background: #f5f7fb;
  display: flex;
  justify-content: center;
  min-height: 320px;
}

.sample-stage img {
  max-height: 320px;
  max-width: 100%;
  object-fit: contain;
}

.sample-strip {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.sample-strip button {
  background: #f8fafc;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
  flex: 0 0 70px;
  height: 58px;
  padding: 3px;
}

.sample-strip button.active {
  border-color: #2563eb;
}

.sample-strip img {
  height: 100%;
  object-fit: cover;
  width: 100%;
}

.class-chart {
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  display: grid;
  gap: 12px;
  padding: 18px;
}

.class-row {
  align-items: center;
  display: grid;
  gap: 12px;
  grid-template-columns: 120px 1fr 64px;
}

.class-row span,
.class-row strong {
  color: #334155;
  font-size: 14px;
}

.class-row div {
  background: #e2e8f0;
  border-radius: 999px;
  height: 10px;
  overflow: hidden;
}

.class-row i {
  background: #2563eb;
  display: block;
  height: 100%;
}

@container data-preparation (max-width: 1120px) {
  .import-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container data-preparation (max-width: 720px) {
  .import-grid,
  .dataset-grid,
  .dataset-grid-compact,
  .modal-form-grid,
  .advanced-grid {
    grid-template-columns: 1fr;
  }

  .workspace-tabs,
  .detail-hero,
  .split-panel {
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

@container data-preparation (max-width: 480px) {
  .workspace-tabs,
  .operation-head {
    gap: 12px;
  }

  .tab-list {
    width: 100%;
    overflow-x: auto;
  }

  .toolbar {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
