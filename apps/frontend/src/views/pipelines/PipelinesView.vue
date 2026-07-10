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
                    v-for="model in matchingBaseModels"
                    :key="model.id"
                    :label="modelLabel(model)"
                    :value="model.id"
                    :disabled="model.status !== 'ready'"
                  />
                </el-select>
              </el-form-item>
              <el-table :data="matchingBaseModels" border empty-text="暂无匹配模型">
                <el-table-column prop="name" label="名称" min-width="180" />
                <el-table-column prop="version" label="版本" width="110" />
                <el-table-column prop="task" label="任务" width="110" />
                <el-table-column prop="scale" label="规模" width="90" />
                <el-table-column label="状态" width="120">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'ready' ? 'success' : 'info'">
                      {{ baseModelStatusLabel(row.status) }}
                    </el-tag>
                  </template>
                </el-table-column>
              </el-table>
              <p v-if="matchingBaseModels.length && !selectableBaseModels.length" class="step-hint">
                当前匹配模型尚未准备完成，请等待系统初始化完成后再创建产线。
              </p>
            </div>

            <div v-show="activeStep === 2" class="step-panel">
              <el-form-item label="数据集" prop="dataset_id">
                <el-select
                  v-model="form.dataset_id"
                  filterable
                  placeholder="选择已校验数据集"
                >
                  <el-option
                    v-for="dataset in preparedDatasets"
                    :key="dataset.id"
                    :label="datasetLabel(dataset)"
                    :value="dataset.id"
                    :disabled="!isDatasetReadyForTraining(dataset)"
                  />
                </el-select>
              </el-form-item>
              <el-table :data="preparedDatasets" border empty-text="暂无匹配数据集">
                <el-table-column prop="name" label="名称" min-width="180" />
                <el-table-column prop="task" label="任务" width="110" />
                <el-table-column label="状态" width="120">
                  <template #default="{ row }">
                    <el-tag :type="isDatasetReadyForTraining(row) ? 'success' : 'info'">
                      {{ datasetStatusLabel(row) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="sample_count" label="样本" width="100" />
                <el-table-column prop="annotation_count" label="标注" width="100" />
              </el-table>
            </div>

            <div v-show="activeStep === 3" class="step-panel">
              <header class="params-header">
                <div>
                  <strong>请设置模型参数</strong>
                  <span>{{ selectedBaseModelName }}</span>
                </div>
                <button type="button" @click="toggleConfigMode">
                  {{ configMode ? "退出修改配置文件" : "修改配置文件" }}
                </button>
              </header>

              <div v-if="!configMode" class="params-form">
                <section class="param-section">
                  <el-form-item label="轮次(Epochs)" prop="epochs">
                    <el-input-number v-model="form.epochs" :min="1" :max="10000" />
                    <p>训练轮次越大，耗时越久，通常更容易收敛。</p>
                  </el-form-item>
                  <el-form-item label="批大小(Batch Size)" prop="batch">
                    <el-input-number v-model="form.batch" :min="-1" :max="1024" />
                    <p>支持固定 batch，也支持 -1 自动 batch。</p>
                  </el-form-item>
                  <el-form-item label="类别数量(Class Num)">
                    <el-input-number v-model="form.class_num" :min="1" :max="100000" disabled />
                    <p>根据所选数据集类别自动推断。</p>
                  </el-form-item>
                  <el-form-item label="学习率(Learning Rate)" prop="lr0">
                    <el-input-number v-model="form.lr0" :min="0.000001" :max="1" :step="0.0001" :precision="6" />
                    <p>Ultralytics 参数名：lr0。</p>
                  </el-form-item>
                  <el-form-item label="图片尺寸(Image Size)" prop="imgsz">
                    <el-input-number v-model="form.imgsz" :min="32" :max="4096" :step="32" />
                    <p>Ultralytics 参数名：imgsz。</p>
                  </el-form-item>
                  <el-form-item label="优化器(Optimizer)">
                    <el-select v-model="form.optimizer">
                      <el-option label="auto" value="auto" />
                      <el-option label="SGD" value="SGD" />
                      <el-option label="MuSGD" value="MuSGD" />
                      <el-option label="Adam" value="Adam" />
                      <el-option label="AdamW" value="AdamW" />
                      <el-option label="RMSProp" value="RMSProp" />
                    </el-select>
                    <p>YOLO26 长训练默认可使用 MuSGD，auto 会自动选择。</p>
                  </el-form-item>
                </section>

                <details class="advanced-params" open>
                  <summary>高级配置</summary>
                  <section class="param-section">
                    <el-form-item label="log打印间隔(Log Interval) / step">
                      <el-input-number v-model="form.log_interval" :min="1" :max="100000" />
                      <p>保存到配置模板中；当前训练命令只传 Ultralytics 支持参数。</p>
                    </el-form-item>
                    <el-form-item label="断点训练权重">
                      <el-select v-model="form.resume">
                        <el-option label="不启用" :value="false" />
                        <el-option label="启用 resume" :value="true" />
                      </el-select>
                      <p>从上一次 checkpoint 继续训练。</p>
                    </el-form-item>
                    <el-form-item label="预训练权重">
                      <el-select v-model="form.pretrained">
                        <el-option label="使用官方权重" :value="true" />
                        <el-option label="不使用预训练" :value="false" />
                      </el-select>
                      <p>从预训练权重开始微调，提高训练效率。</p>
                    </el-form-item>
                    <el-form-item label="热启动轮次(WarmUp Epochs)">
                      <el-input-number v-model="form.warmup_epochs" :min="0" :max="10000" :step="0.5" />
                    </el-form-item>
                    <el-form-item label="保存间隔(Save Interval) / epoch">
                      <el-input-number v-model="form.save_period" :min="-1" :max="10000" />
                    </el-form-item>
                    <el-form-item label="早停耐心(Patience)">
                      <el-input-number v-model="form.patience" :min="0" :max="10000" />
                    </el-form-item>
                    <el-form-item label="最终学习率比例(lrf)">
                      <el-input-number v-model="form.lrf" :min="0" :max="1" :step="0.01" :precision="4" />
                    </el-form-item>
                    <el-form-item label="动量(Momentum)">
                      <el-input-number v-model="form.momentum" :min="0" :max="1" :step="0.001" :precision="4" />
                    </el-form-item>
                    <el-form-item label="权重衰减(Weight Decay)">
                      <el-input-number v-model="form.weight_decay" :min="0" :max="1" :step="0.0001" :precision="6" />
                    </el-form-item>
                    <el-form-item label="workers">
                      <el-input-number v-model="form.workers" :min="0" :max="256" />
                    </el-form-item>
                    <el-form-item label="设备(Device)">
                      <el-select v-model="form.device">
                        <el-option label="auto" value="auto" />
                        <el-option label="cpu" value="cpu" />
                        <el-option label="cuda:0" value="cuda:0" />
                        <el-option label="0" value="0" />
                      </el-select>
                    </el-form-item>
                    <el-form-item label="AMP">
                      <el-switch v-model="form.amp" />
                    </el-form-item>
                    <el-form-item label="余弦学习率(cos_lr)">
                      <el-switch v-model="form.cos_lr" />
                    </el-form-item>
                    <el-form-item label="mosaic关闭轮次(close_mosaic)">
                      <el-input-number v-model="form.close_mosaic" :min="0" :max="10000" />
                    </el-form-item>
                    <el-form-item label="box loss">
                      <el-input-number v-model="form.box" :min="0" :max="1000" :step="0.1" :precision="4" />
                    </el-form-item>
                    <el-form-item label="cls loss">
                      <el-input-number v-model="form.cls" :min="0" :max="1000" :step="0.1" :precision="4" />
                    </el-form-item>
                    <el-form-item label="dfl loss">
                      <el-input-number v-model="form.dfl" :min="0" :max="1000" :step="0.1" :precision="4" />
                    </el-form-item>
                  </section>

                  <section class="augment-grid">
                    <label v-for="item in augmentationFields" :key="item.key">
                      <span>{{ item.label }}</span>
                      <input v-model.number="form[item.key]" type="number" min="0" max="1" step="0.01" />
                    </label>
                  </section>
                </details>
              </div>

              <div v-else class="config-editor">
                <textarea v-model="configText" spellcheck="false" />
                <div class="config-actions">
                  <el-button @click="syncConfigFromForm">恢复为当前表单配置</el-button>
                  <el-button type="primary" @click="applyConfigText">应用配置文件</el-button>
                </div>
              </div>
            </div>

            <div v-if="false" class="step-panel">
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
  class_schema?: { names?: unknown[] };
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

type TrainForm = {
  [key: string]: string | number | boolean;
  name: string;
  description: string;
  task: string;
  scale: string;
  base_model_id: string;
  dataset_id: string;
  epochs: number;
  imgsz: number;
  batch: number;
  class_num: number;
  lr0: number;
  optimizer: string;
  log_interval: number;
  resume: boolean;
  pretrained: boolean;
  warmup_epochs: number;
  save_period: number;
  patience: number;
  lrf: number;
  momentum: number;
  weight_decay: number;
  workers: number;
  device: string;
  amp: boolean;
  cos_lr: boolean;
  close_mosaic: number;
  box: number;
  cls: number;
  dfl: number;
};
type ConfigParamValue = string | number | boolean | number[];

const taskOptions = ["detect", "segment", "semantic", "pose", "obb", "classify"];
const scaleOptions = ["n", "s", "m", "l", "x"];
const augmentationFields = [
  { key: "hsv_h", label: "hsv_h" },
  { key: "hsv_s", label: "hsv_s" },
  { key: "hsv_v", label: "hsv_v" },
  { key: "degrees", label: "degrees" },
  { key: "translate", label: "translate" },
  { key: "scale_aug", label: "scale" },
  { key: "shear", label: "shear" },
  { key: "perspective", label: "perspective" },
  { key: "flipud", label: "flipud" },
  { key: "fliplr", label: "fliplr" },
  { key: "mosaic", label: "mosaic" },
  { key: "mixup", label: "mixup" },
  { key: "copy_paste", label: "copy_paste" },
] as const;

const formRef = ref<FormInstance>();
const activeStep = ref(0);
const loading = ref(false);
const submitting = ref(false);
const errorMessage = ref("");
const configMode = ref(false);
const configText = ref("");
const extraConfigParams = ref<Record<string, ConfigParamValue>>({});

const baseModels = ref<BaseModelRow[]>([]);
const datasets = ref<DatasetRow[]>([]);
const pipelines = ref<PipelineRow[]>([]);
const trainingJobs = ref<TrainingJobRow[]>([]);

const form = reactive<TrainForm>({
  name: "",
  description: "",
  task: "detect",
  scale: "n",
  base_model_id: "",
  dataset_id: "",
  epochs: 50,
  imgsz: 640,
  batch: 16,
  class_num: 1,
  lr0: 0.01,
  optimizer: "auto",
  log_interval: 10,
  resume: false,
  pretrained: true,
  warmup_epochs: 3,
  save_period: -1,
  patience: 100,
  lrf: 0.01,
  momentum: 0.937,
  weight_decay: 0.0005,
  workers: 8,
  device: "auto",
  amp: true,
  cos_lr: false,
  close_mosaic: 10,
  box: 7.5,
  cls: 0.5,
  dfl: 1.5,
  hsv_h: 0.015,
  hsv_s: 0.7,
  hsv_v: 0.4,
  degrees: 0,
  translate: 0.1,
  scale_aug: 0.5,
  shear: 0,
  perspective: 0,
  flipud: 0,
  fliplr: 0.5,
  mosaic: 1,
  mixup: 0,
  copy_paste: 0,
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

rules.lr0 = [{ required: true, type: "number", message: "请输入学习率", trigger: "change" }];

const matchingBaseModels = computed(() =>
  baseModels.value.filter(
    (model) =>
      model.task === form.task &&
      model.scale === form.scale,
  ),
);

const selectableBaseModels = computed(() =>
  matchingBaseModels.value.filter((model) => !model.status || model.status === "ready"),
);

const preparedDatasets = computed(() =>
  datasets.value.filter((dataset) => dataset.task === form.task && isDatasetPrepared(dataset)),
);

const selectableDatasets = computed(() =>
  preparedDatasets.value.filter((dataset) => isDatasetReadyForTraining(dataset)),
);

const selectedBaseModelName = computed(() => {
  const model = baseModels.value.find((item) => item.id === form.base_model_id);
  return model ? modelLabel(model) : "YOLO26";
});

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
  if (activeStep.value === 1) {
    const selectedModel = baseModels.value.find((model) => model.id === form.base_model_id);
    if (selectedModel && selectedModel.status !== "ready") {
      ElMessage.warning("基础模型尚未准备完成，请等待状态变为可用");
      return;
    }
  }
  const fieldsByStep = [
    ["name", "task", "scale"],
    ["base_model_id"],
    ["dataset_id"],
    ["epochs", "imgsz", "batch", "lr0"],
  ];
  await formRef.value?.validateField(fieldsByStep[activeStep.value]);
  if (activeStep.value === 2) {
    updateClassCountFromDataset();
  }
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
        params_template: trainingParams(),
        default_environment: trainingEnvironment(),
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
  updateClassCountFromDataset();
  const params: Record<string, ConfigParamValue> = {
    ...extraConfigParams.value,
    amp: form.amp,
    batch: form.batch,
    box: form.box,
    close_mosaic: form.close_mosaic,
    cls: form.cls,
    copy_paste: form.copy_paste,
    cos_lr: form.cos_lr,
    degrees: form.degrees,
    dfl: form.dfl,
    epochs: form.epochs,
    fliplr: form.fliplr,
    flipud: form.flipud,
    hsv_h: form.hsv_h,
    hsv_s: form.hsv_s,
    hsv_v: form.hsv_v,
    imgsz: form.imgsz,
    lr0: form.lr0,
    lrf: form.lrf,
    mixup: form.mixup,
    momentum: form.momentum,
    mosaic: form.mosaic,
    optimizer: form.optimizer,
    patience: form.patience,
    perspective: form.perspective,
    pretrained: form.pretrained,
    resume: form.resume,
    save_period: form.save_period,
    scale: form.scale_aug,
    shear: form.shear,
    translate: form.translate,
    warmup_epochs: form.warmup_epochs,
    weight_decay: form.weight_decay,
  };
  return removeEmptyParams(params);
}

function updateClassCountFromDataset() {
  const dataset = datasets.value.find((item) => item.id === form.dataset_id);
  const names = dataset?.class_schema?.names;
  if (Array.isArray(names) && names.length > 0) {
    form.class_num = names.length;
  }
}

function trainingEnvironment() {
  return {
    device: form.device,
    workers: form.workers,
  };
}

function toggleConfigMode() {
  if (!configMode.value) {
    syncConfigFromForm();
  }
  configMode.value = !configMode.value;
}

function syncConfigFromForm() {
  configText.value = stringifyConfig({
    ...trainingParams(),
    ...trainingEnvironment(),
    log_interval: form.log_interval,
  });
}

function applyConfigText() {
  try {
    const parsed = parseConfigText(configText.value);
    const extra: Record<string, ConfigParamValue> = {};
    for (const [key, value] of Object.entries(parsed)) {
      const formKey = key === "scale" ? "scale_aug" : key;
      if (formKey in form && !Array.isArray(value)) {
        form[formKey] = value;
      } else if (!["model", "data", "project", "name", "exist_ok", "save_dir"].includes(key)) {
        extra[key] = value;
      }
    }
    extraConfigParams.value = extra;
    ElMessage.success("配置文件已应用");
    configMode.value = false;
  } catch (error) {
    ElMessage.error(getErrorMessage(error, "配置文件解析失败"));
  }
}

function stringifyConfig(params: Record<string, unknown>) {
  return Object.entries(params)
    .map(([key, value]) => `${key}: ${formatConfigValue(value)}`)
    .join("\n");
}

function formatConfigValue(value: unknown) {
  if (Array.isArray(value)) return `[${value.join(", ")}]`;
  return String(value);
}

function parseConfigText(text: string) {
  const result: Record<string, ConfigParamValue> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf(":");
    if (separator <= 0) {
      throw new Error(`配置行格式错误：${rawLine}`);
    }
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    result[key] = parseConfigValue(value);
  }
  return result;
}

function parseConfigValue(value: string): ConfigParamValue {
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^\[[\d,\s-]+\]$/.test(value)) {
    return value
      .slice(1, -1)
      .split(",")
      .map((item) => Number(item.trim()))
      .filter((item) => Number.isFinite(item));
  }
  if (value && !Number.isNaN(Number(value))) return Number(value);
  return value.replace(/^["']|["']$/g, "");
}

function removeEmptyParams(params: Record<string, ConfigParamValue>) {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== "" && value !== undefined && value !== null),
  );
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
  return [model.name || model.id, model.version, model.task, model.scale, baseModelStatusLabel(model.status)]
    .filter(Boolean)
    .join(" / ");
}

function baseModelStatusLabel(status?: string) {
  const labels: Record<string, string> = {
    failed: "不可用",
    pending: "准备中",
    ready: "可用",
  };
  return labels[String(status || "")] || status || "unknown";
}

function datasetLabel(dataset: DatasetRow) {
  const sampleCount =
    typeof dataset.sample_count === "number" ? `${dataset.sample_count} samples` : "";
  return [dataset.name || dataset.id, dataset.task, sampleCount, datasetStatusLabel(dataset)].filter(Boolean).join(" / ");
}

function isDatasetPrepared(dataset: DatasetRow) {
  return numberValue(dataset.sample_count) > 0 && numberValue(dataset.annotation_count) > 0;
}

function isDatasetReadyForTraining(dataset: DatasetRow) {
  return dataset.status === "validated";
}

function datasetStatusLabel(dataset: DatasetRow) {
  if (dataset.status === "validated") return "已校验";
  if (isDatasetPrepared(dataset)) return "待校验";
  return dataset.status || "unknown";
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

.step-hint {
  color: #6b7280;
  font-size: 13px;
  line-height: 1.5;
  margin: 12px 0 0;
}

.params-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
  margin-bottom: 18px;
}

.params-header strong {
  color: #0f172a;
  font-size: 20px;
  line-height: 1.2;
}

.params-header span {
  color: #64748b;
  font-size: 13px;
  margin-left: 8px;
}

.params-header button {
  background: transparent;
  border: 0;
  color: #2563eb;
  cursor: pointer;
  font-size: 14px;
  padding: 6px 0;
}

.params-form,
.param-section {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.param-section :deep(.el-form-item) {
  align-items: flex-start;
  display: grid;
  gap: 6px;
  margin-bottom: 0;
  max-width: 560px;
}

.param-section :deep(.el-form-item__label) {
  color: #0f172a;
  font-size: 16px;
  justify-content: flex-start;
  margin-bottom: 4px;
  width: auto !important;
}

.param-section :deep(.el-form-item__content) {
  align-items: stretch;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-left: 0 !important;
}

.param-section :deep(.el-input-number),
.param-section :deep(.el-select) {
  max-width: 540px;
  width: 100%;
}

.param-section p {
  color: #64748b;
  font-size: 13px;
  line-height: 1.45;
  margin: 0;
}

.advanced-params {
  border-top: 1px solid #e5e7eb;
  padding-top: 16px;
}

.advanced-params summary {
  color: #0f172a;
  cursor: pointer;
  font-size: 17px;
  font-weight: 700;
  margin-bottom: 16px;
}

.augment-grid {
  border-top: 1px solid #eef2f7;
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(3, minmax(140px, 1fr));
  margin-top: 18px;
  padding-top: 18px;
}

.augment-grid label {
  display: grid;
  gap: 6px;
}

.augment-grid span {
  color: #334155;
  font-size: 13px;
}

.augment-grid input {
  border: 1px solid #d1d5db;
  border-radius: 4px;
  color: #0f172a;
  height: 34px;
  padding: 0 8px;
}

.config-editor {
  border: 1px solid #2563eb;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 10px;
}

.config-editor textarea {
  border: 0;
  color: #0f172a;
  font-family: Consolas, "Courier New", monospace;
  font-size: 13px;
  line-height: 1.55;
  min-height: 560px;
  outline: none;
  resize: vertical;
  width: 100%;
}

.config-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

</style>
