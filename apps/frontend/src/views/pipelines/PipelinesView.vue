<template>
  <section class="pipelines-view">
    <header class="page-header">
      <div>
        <p class="eyebrow">训练产线</p>
        <h1>YOLO26 训练向导</h1>
      </div>
      <el-button :loading="loading" @click="loadWorkspace">刷新</el-button>
    </header>

    <el-alert
      v-if="errorMessage"
      type="warning"
      :title="errorMessage"
      show-icon
      :closable="false"
    />

    <el-row :gutter="16">
      <el-col :xs="24" :lg="15">
        <el-card shadow="never" class="wizard-card">
          <el-steps :active="activeStep" finish-status="success" simple>
            <el-step title="产线信息" />
            <el-step title="基础模型" />
            <el-step title="数据集" />
            <el-step title="训练参数" />
          </el-steps>

          <el-form
            ref="formRef"
            class="wizard-form"
            :model="form"
            :rules="rules"
            label-width="96px"
          >
            <div v-show="activeStep === 0" class="step-panel">
              <el-form-item label="名称" prop="name">
                <el-input v-model="form.name" placeholder="例如：产线-安全帽检测-v1" />
              </el-form-item>
              <el-form-item label="任务" prop="task">
                <el-select v-model="form.task" placeholder="选择任务">
                  <el-option
                    v-for="task in taskOptions"
                    :key="task"
                    :label="task"
                    :value="task"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="规模" prop="scale">
                <el-select v-model="form.scale" placeholder="选择模型规模">
                  <el-option
                    v-for="scale in scaleOptions"
                    :key="scale"
                    :label="scale"
                    :value="scale"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="说明">
                <el-input
                  v-model="form.description"
                  type="textarea"
                  :rows="4"
                  placeholder="记录训练目标、数据范围或验收口径"
                />
              </el-form-item>
            </div>

            <div v-show="activeStep === 1" class="step-panel">
              <el-form-item label="基础模型" prop="base_model_id">
                <el-select
                  v-model="form.base_model_id"
                  filterable
                  placeholder="选择可用基础模型"
                >
                  <el-option
                    v-for="model in selectableBaseModels"
                    :key="model.id"
                    :label="modelLabel(model)"
                    :value="model.id"
                  />
                </el-select>
              </el-form-item>
              <el-table :data="selectableBaseModels" border empty-text="暂无匹配模型">
                <el-table-column prop="name" label="名称" min-width="180" />
                <el-table-column prop="version" label="版本" width="110" />
                <el-table-column prop="task" label="任务" width="110" />
                <el-table-column prop="scale" label="规模" width="90" />
                <el-table-column label="状态" width="120">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'ready' ? 'success' : 'info'">
                      {{ row.status || "unknown" }}
                    </el-tag>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div v-show="activeStep === 2" class="step-panel">
              <el-form-item label="数据集" prop="dataset_id">
                <el-select
                  v-model="form.dataset_id"
                  filterable
                  placeholder="选择已校验数据集"
                >
                  <el-option
                    v-for="dataset in selectableDatasets"
                    :key="dataset.id"
                    :label="datasetLabel(dataset)"
                    :value="dataset.id"
                  />
                </el-select>
              </el-form-item>
              <el-table :data="selectableDatasets" border empty-text="暂无匹配数据集">
                <el-table-column prop="name" label="名称" min-width="180" />
                <el-table-column prop="task" label="任务" width="110" />
                <el-table-column label="状态" width="120">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'validated' ? 'success' : 'info'">
                      {{ row.status || "unknown" }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="sample_count" label="样本" width="100" />
                <el-table-column prop="annotation_count" label="标注" width="100" />
              </el-table>
            </div>

            <div v-show="activeStep === 3" class="step-panel">
              <el-form-item label="Epochs" prop="epochs">
                <el-input-number v-model="form.epochs" :min="1" :max="500" />
              </el-form-item>
              <el-form-item label="图片尺寸" prop="imgsz">
                <el-input-number v-model="form.imgsz" :min="128" :max="2048" :step="32" />
              </el-form-item>
              <el-form-item label="Batch" prop="batch">
                <el-input-number v-model="form.batch" :min="1" :max="128" />
              </el-form-item>
              <el-form-item label="设备">
                <el-select v-model="form.device">
                  <el-option label="auto" value="auto" />
                  <el-option label="cpu" value="cpu" />
                  <el-option label="cuda:0" value="cuda:0" />
                </el-select>
              </el-form-item>
              <el-form-item label="半精度">
                <el-switch v-model="form.half" />
              </el-form-item>
            </div>
          </el-form>

          <footer class="wizard-actions">
            <el-button :disabled="activeStep === 0" @click="activeStep -= 1">
              上一步
            </el-button>
            <el-button v-if="activeStep < 3" type="primary" @click="goNext">
              下一步
            </el-button>
            <el-button
              v-else
              type="primary"
              :loading="submitting"
              @click="createPipelineAndJob"
            >
              创建训练 Job
            </el-button>
          </footer>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="9">
        <div class="side-stack">
          <el-card shadow="never">
            <template #header>已有产线</template>
            <el-table :data="pipelines" size="small" empty-text="暂无产线">
              <el-table-column prop="name" label="名称" min-width="150" />
              <el-table-column prop="task" label="任务" width="100" />
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <el-tag :type="statusTag(row.status)">
                    {{ row.status || "unknown" }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="90">
                <template #default="{ row }">
                  <el-button size="small" link type="primary" @click="usePipeline(row)">
                    使用
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card shadow="never">
            <template #header>最近训练 Jobs</template>
            <el-table :data="trainingJobs" size="small" empty-text="暂无训练 Job">
              <el-table-column prop="id" label="Job ID" min-width="180" show-overflow-tooltip />
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <el-tag :type="statusTag(row.status)">
                    {{ row.status || "unknown" }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="创建时间" width="160">
                <template #default="{ row }">
                  {{ formatTime(row.created_at) }}
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>
      </el-col>
    </el-row>
  </section>
</template>

<script setup lang="ts">
import type { FormInstance, FormRules } from "element-plus";
import { ElMessage } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";

import { api } from "@/api/client";

type AnyRecord = Record<string, unknown>;

interface BaseModelRow extends AnyRecord {
  id: string;
  name?: string;
  version?: string;
  task?: string;
  scale?: string;
  status?: string;
}

interface DatasetRow extends AnyRecord {
  id: string;
  name?: string;
  task?: string;
  status?: string;
  sample_count?: number;
  annotation_count?: number;
}

interface PipelineRow extends AnyRecord {
  id: string;
  name?: string;
  task?: string;
  scale?: string;
  status?: string;
  base_model_id?: string;
  dataset_id?: string;
}

interface TrainingJobRow extends AnyRecord {
  id: string;
  status?: string;
  created_at?: string;
}

const taskOptions = ["detect", "segment", "semantic", "pose", "obb", "classify"];
const scaleOptions = ["n", "s", "m", "l", "x"];

const formRef = ref<FormInstance>();
const activeStep = ref(0);
const loading = ref(false);
const submitting = ref(false);
const errorMessage = ref("");

const baseModels = ref<BaseModelRow[]>([]);
const datasets = ref<DatasetRow[]>([]);
const pipelines = ref<PipelineRow[]>([]);
const trainingJobs = ref<TrainingJobRow[]>([]);

const form = reactive({
  name: "",
  description: "",
  task: "detect",
  scale: "n",
  base_model_id: "",
  dataset_id: "",
  epochs: 50,
  imgsz: 640,
  batch: 16,
  device: "auto",
  half: false,
});

const rules: FormRules = {
  name: [{ required: true, message: "请输入产线名称", trigger: "blur" }],
  task: [{ required: true, message: "请选择任务", trigger: "change" }],
  scale: [{ required: true, message: "请选择规模", trigger: "change" }],
  base_model_id: [{ required: true, message: "请选择基础模型", trigger: "change" }],
  dataset_id: [{ required: true, message: "请选择数据集", trigger: "change" }],
  epochs: [{ required: true, type: "number", message: "请输入 epochs", trigger: "change" }],
  imgsz: [{ required: true, type: "number", message: "请输入图片尺寸", trigger: "change" }],
  batch: [{ required: true, type: "number", message: "请输入 batch", trigger: "change" }],
};

const selectableBaseModels = computed(() =>
  baseModels.value.filter(
    (model) =>
      model.task === form.task &&
      model.scale === form.scale &&
      (!model.status || model.status === "ready"),
  ),
);

const selectableDatasets = computed(() =>
  datasets.value.filter(
    (dataset) =>
      dataset.task === form.task && (!dataset.status || dataset.status === "validated"),
  ),
);

onMounted(() => {
  void loadWorkspace();
});

async function loadWorkspace() {
  loading.value = true;
  errorMessage.value = "";

  const [baseResult, datasetResult, pipelineResult, jobResult] = await Promise.allSettled([
    api.listBaseModels(),
    api.listDatasets(),
    api.listPipelines(),
    api.listTrainingJobs(),
  ]);

  baseModels.value = unwrapResult<BaseModelRow>(baseResult, "基础模型");
  datasets.value = unwrapResult<DatasetRow>(datasetResult, "数据集");
  pipelines.value = unwrapResult<PipelineRow>(pipelineResult, "产线");
  trainingJobs.value = unwrapResult<TrainingJobRow>(jobResult, "训练 Job");

  loading.value = false;
}

async function goNext() {
  const fieldsByStep = [
    ["name", "task", "scale"],
    ["base_model_id"],
    ["dataset_id"],
    ["epochs", "imgsz", "batch"],
  ];
  await formRef.value?.validateField(fieldsByStep[activeStep.value]);
  activeStep.value += 1;
}

async function createPipelineAndJob() {
  if (!formRef.value) return;
  await formRef.value.validate();
  submitting.value = true;

  try {
    const pipeline = normalizeRecord<PipelineRow>(
      await api.createPipeline({
        name: form.name,
        description: form.description,
        task: form.task,
        scale: form.scale,
        base_model_id: form.base_model_id,
        dataset_id: form.dataset_id,
        params: trainingParams(),
      }),
    );
    const pipelineId = pipeline.id || String(pipeline.pipeline_id || "");
    if (!pipelineId) {
      throw new Error("产线创建成功但响应缺少 pipeline id");
    }

    await api.createTrainingJob(pipelineId, {});
    ElMessage.success("已创建训练 Job");
    await loadWorkspace();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "训练 Job 创建失败"));
  } finally {
    submitting.value = false;
  }
}

async function usePipeline(row: PipelineRow) {
  if (!row.id) return;
  submitting.value = true;
  try {
    await api.createTrainingJob(row.id, {});
    ElMessage.success("已基于现有产线创建训练 Job");
    await loadWorkspace();
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "训练 Job 创建失败"));
  } finally {
    submitting.value = false;
  }
}

function trainingParams() {
  return {
    epochs: form.epochs,
    imgsz: form.imgsz,
    batch: form.batch,
    device: form.device,
    half: form.half,
  };
}

function unwrapResult<T extends AnyRecord>(
  result: PromiseSettledResult<unknown>,
  label: string,
) {
  if (result.status === "fulfilled") return normalizeRows<T>(result.value);
  errorMessage.value = errorMessage.value
    ? `${errorMessage.value} ${label}加载失败。`
    : `${label}加载失败，相关区域已显示空表。`;
  return [];
}

function normalizeRows<T extends AnyRecord>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (!payload || typeof payload !== "object") return [];
  const record = payload as AnyRecord;
  for (const key of ["items", "data", "results", "pipelines", "jobs", "models", "datasets"]) {
    const value = record[key];
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

function normalizeRecord<T extends AnyRecord>(payload: unknown): T {
  if (!payload || typeof payload !== "object") return {} as T;
  const record = payload as AnyRecord;
  for (const key of ["data", "item", "pipeline"]) {
    const value = record[key];
    if (value && typeof value === "object" && !Array.isArray(value)) return value as T;
  }
  return record as T;
}

function modelLabel(model: BaseModelRow) {
  return [model.name || model.id, model.version, model.task, model.scale]
    .filter(Boolean)
    .join(" / ");
}

function datasetLabel(dataset: DatasetRow) {
  const sampleCount =
    typeof dataset.sample_count === "number" ? `${dataset.sample_count} samples` : "";
  return [dataset.name || dataset.id, dataset.task, sampleCount].filter(Boolean).join(" / ");
}

function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function statusTag(status?: string) {
  if (status === "ready" || status === "success" || status === "running") return "success";
  if (status === "failed") return "danger";
  if (status === "queued" || status === "training" || status === "pending") return "warning";
  return "info";
}

function getErrorMessage(error: unknown, fallback: string) {
  if (error instanceof Error) return error.message;
  return fallback;
}
</script>

<style scoped>
.pipelines-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
}

.page-header,
.wizard-actions {
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

.wizard-card {
  min-height: 640px;
}

.wizard-form {
  margin-top: 24px;
}

.step-panel {
  min-height: 410px;
}

.wizard-actions {
  border-top: 1px solid #e5e7eb;
  margin-top: 16px;
  padding-top: 16px;
}

.side-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
