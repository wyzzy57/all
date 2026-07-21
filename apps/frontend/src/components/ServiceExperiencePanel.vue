<template>
  <section class="experience-panel">
    <aside class="test-images">
      <h2>选择测试图像</h2>
      <img class="selected-test-image" :src="selectedExample.image" :alt="selectedExample.name" />

      <div class="thumb-grid">
        <button
          v-for="example in allTestExamples"
          :key="example.id"
          :class="{ active: selectedExampleId === example.id }"
          type="button"
          @click="selectExample(example.id)"
        >
          <img :src="example.thumb" :alt="example.name" />
        </button>
        <label class="upload-example" for="shared-service-example-upload">
          <input
            id="shared-service-example-upload"
            type="file"
            accept="image/jpeg,image/png,image/tiff,image/bmp"
            @change="handleExampleUpload"
          />
          <span aria-hidden="true">↥</span>
        </label>
      </div>

      <p>支持用户上传测试图像（.jpeg /.jpg /.png /.tiff /.tif /.bmp /.pdf文件格式），文件体积不超过10MB。</p>

      <div v-if="showInferenceControls" class="inference-fields">
        <label class="inference-field required" for="experience-model-weight">
          <span class="field-title">模型方案</span>
          <select id="experience-model-weight" v-model="selectedModelWeight" class="field-control field-select">
            <option value="" disabled>请选择模型方案</option>
            <option v-for="option in modelOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <label class="inference-field required" for="experience-environment">
          <span class="field-title">选择环境</span>
          <input id="experience-environment" v-model="selectedEnvironment" class="field-control" list="inference-environments" />
          <datalist id="inference-environments">
            <option v-for="option in environmentOptions" :key="option" :value="option" />
          </datalist>
        </label>
      </div>

      <div class="experience-actions">
        <button type="button" class="primary-action" :disabled="running" @click="runExperience">
          {{ running ? "运行中" : "运行" }}
        </button>
        <button type="button" class="secondary-action" @click="resetExperience">重置</button>
      </div>
      <p v-if="errorMessage" class="experience-error">{{ errorMessage }}</p>
    </aside>

    <main class="result-panel">
      <div class="result-header">
        <span>运行结果</span>
        <div>
          <button :class="{ active: resultMode === 'image' }" type="button" @click="resultMode = 'image'">图片</button>
          <button :class="{ active: resultMode === 'json' }" type="button" @click="resultMode = 'json'">JSON</button>
        </div>
      </div>
      <div class="result-body" :class="{ empty: !hasRun }">
        <template v-if="hasRun">
          <img v-if="resultMode === 'image'" :src="resultImage" alt="运行结果" />
          <pre v-else>{{ resultJson }}</pre>
        </template>
        <div v-else class="empty-result">
          <strong>您还未进行任何测试！</strong>
          <span>快去左侧上传或选择样例，测试一下模型效果吧</span>
        </div>
      </div>
    </main>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";

type ResultMode = "image" | "json";

type TestExample = {
  id: string;
  name: string;
  image: string;
  thumb: string;
};

type ModelOption = {
  label: string;
  value: string;
};

export type ExperienceInferenceRequest = {
  file: File;
  modelWeight: string;
  environment: string;
};

export type ExperienceInferenceResponse = {
  result_image: string;
  predictions: unknown[];
  [key: string]: unknown;
};

const props = withDefaults(
  defineProps<{
    serviceName?: string;
    showInferenceControls?: boolean;
    modelOptions?: ModelOption[];
    environmentOptions?: string[];
    defaultEnvironment?: string;
    runInference?: (request: ExperienceInferenceRequest) => Promise<ExperienceInferenceResponse>;
  }>(),
  {
    serviceName: "在线服务",
    showInferenceControls: false,
    modelOptions: () => [],
    environmentOptions: () => ["cpu"],
    defaultEnvironment: "cpu",
    runInference: undefined,
  },
);

const sampleExamples: TestExample[] = [
  { id: "anime-group", name: "人物样例", image: "/service-examples/anime-group.png", thumb: "/service-examples/anime-group.png" },
  { id: "pandas", name: "动物样例", image: "/service-examples/pandas.png", thumb: "/service-examples/pandas.png" },
  { id: "document-flow", name: "文档样例", image: "/service-examples/document-flow.png", thumb: "/service-examples/document-flow.png" },
  { id: "snowboard", name: "运动样例", image: "/service-examples/snowboard.png", thumb: "/service-examples/snowboard.png" },
  { id: "cats", name: "宠物样例", image: "/service-examples/cats.png", thumb: "/service-examples/cats.png" },
  { id: "living-room", name: "室内样例", image: "/service-examples/living-room.png", thumb: "/service-examples/living-room.png" },
  { id: "drinks", name: "餐饮样例", image: "/service-examples/drinks.png", thumb: "/service-examples/drinks.png" },
  { id: "city-traffic", name: "交通样例", image: "/service-examples/city-traffic.png", thumb: "/service-examples/city-traffic.png" },
  { id: "fruit-basket", name: "水果样例", image: "/service-examples/fruit-basket.png", thumb: "/service-examples/fruit-basket.png" },
];

const selectedExampleId = ref("anime-group");
const resultMode = ref<ResultMode>("image");
const hasRun = ref(false);
const running = ref(false);
const errorMessage = ref("");
const selectedModelWeight = ref("");
const selectedEnvironment = ref(props.defaultEnvironment);
const uploadedExamples = ref<TestExample[]>([]);
const inferenceResult = ref<ExperienceInferenceResponse | null>(null);

const allTestExamples = computed(() => [...sampleExamples, ...uploadedExamples.value]);
const selectedExample = computed(() => allTestExamples.value.find((item) => item.id === selectedExampleId.value) ?? sampleExamples[0]);
const resultImage = computed(() => inferenceResult.value?.result_image || svgResultImage());
const resultJson = computed(() =>
  JSON.stringify(
    inferenceResult.value ?? {
      service: props.serviceName,
      image: selectedExample.value.name,
      detections: [
        { label: "0", score: 0.68, box: [120, 42, 640, 358] },
        { label: "1", score: 0.64, box: [620, 262, 690, 344] },
      ],
    },
    null,
    2,
  ),
);

watch(
  () => props.modelOptions,
  (options) => {
    if (!selectedModelWeight.value && options.length > 0) selectedModelWeight.value = options[0].value;
  },
  { immediate: true },
);

watch(
  () => props.defaultEnvironment,
  (environment) => {
    if (!selectedEnvironment.value) selectedEnvironment.value = environment;
  },
);

function selectExample(id: string) {
  selectedExampleId.value = id;
  inferenceResult.value = null;
  hasRun.value = false;
}

async function runExperience() {
  errorMessage.value = "";
  if (props.runInference) {
    if (props.showInferenceControls && !selectedModelWeight.value) {
      errorMessage.value = "请选择模型方案";
      return;
    }
    if (props.showInferenceControls && !selectedEnvironment.value.trim()) {
      errorMessage.value = "请选择环境";
      return;
    }
    running.value = true;
    try {
      const file = await selectedExampleFile();
      inferenceResult.value = await props.runInference({
        file,
        modelWeight: selectedModelWeight.value,
        environment: selectedEnvironment.value.trim(),
      });
      hasRun.value = true;
      resultMode.value = "image";
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "推理失败";
    } finally {
      running.value = false;
    }
    return;
  }
  hasRun.value = true;
  resultMode.value = "image";
}

function resetExperience() {
  hasRun.value = false;
  resultMode.value = "image";
  selectedExampleId.value = sampleExamples[0].id;
  inferenceResult.value = null;
  errorMessage.value = "";
}

function handleExampleUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;

  const imageUrl = URL.createObjectURL(file);
  const uploadedExample = {
    id: `upload-${Date.now()}`,
    name: file.name,
    image: imageUrl,
    thumb: imageUrl,
  };
  uploadedExamples.value = [...uploadedExamples.value, uploadedExample];
  selectedExampleId.value = uploadedExample.id;
  hasRun.value = false;
  inferenceResult.value = null;
  resultMode.value = "image";
  input.value = "";
}

async function selectedExampleFile() {
  const response = await fetch(selectedExample.value.image);
  const blob = await response.blob();
  const filename = `${selectedExample.value.id}.${blob.type.includes("png") ? "png" : "jpg"}`;
  return new File([blob], filename, { type: blob.type || "image/png" });
}

function svgResultImage() {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" width="840" height="472" viewBox="0 0 840 472">
  <rect width="840" height="472" fill="#dbeafe"/>
  <rect x="0" y="0" width="840" height="160" fill="#cbd5e1"/>
  <path d="M0 352 C160 256 284 420 448 324 C608 232 704 356 840 272 L840 472 L0 472 Z" fill="#86efac"/>
  <rect x="120" y="224" width="600" height="148" rx="12" fill="#65a30d"/>
  <circle cx="192" cy="304" r="44" fill="#84cc16"/>
  <circle cx="292" cy="296" r="48" fill="#84cc16"/>
  <circle cx="396" cy="308" r="46" fill="#84cc16"/>
  <circle cx="500" cy="300" r="44" fill="#84cc16"/>
  <circle cx="604" cy="308" r="48" fill="#84cc16"/>
  <rect x="88" y="172" width="656" height="164" fill="none" stroke="#ef4444" stroke-width="6"/>
  <rect x="496" y="56" width="144" height="48" fill="#ef4444"/><text x="512" y="92" fill="#fff" font-size="34">0 0.68</text>
  <rect x="624" y="264" width="88" height="88" fill="none" stroke="#22c55e" stroke-width="6"/>
  <rect x="624" y="224" width="124" height="44" fill="#22c55e"/><text x="636" y="258" fill="#111827" font-size="30">1 0.64</text>
</svg>`)}`;
}
</script>

<style scoped>
.experience-panel {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 24px;
  min-height: 560px;
}

.test-images {
  border-right: 1px solid #98a2b3;
  padding-right: 22px;
}

.test-images h2,
.result-header span {
  margin: 0 0 14px;
  font-size: 16px;
  font-weight: 500;
}

.selected-test-image {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #f4f7fb;
  object-fit: cover;
}

.thumb-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin: 12px 0;
}

.thumb-grid button {
  border: 1px solid transparent;
  border-radius: 4px;
  background: #f4f7fb;
  padding: 0;
  cursor: pointer;
}

.thumb-grid button.active {
  border-color: #1763ff;
  box-shadow: 0 0 0 1px #1763ff inset;
}

.thumb-grid img {
  display: block;
  width: 100%;
  aspect-ratio: 1 / 1;
  border-radius: 3px;
  object-fit: cover;
}

.upload-example {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 1 / 1;
  border: 1px dashed #d0d5dd;
  border-radius: 4px;
  background: #fff;
  color: #344054;
  cursor: pointer;
  font-size: 28px;
}

.upload-example input {
  display: none;
}

.upload-example:hover {
  border-color: #1763ff;
  color: #1763ff;
  background: #f5f9ff;
}

.test-images p {
  color: #98a2b3;
  font-size: 13px;
  line-height: 1.7;
}

.inference-fields {
  display: grid;
  gap: 18px;
  margin-top: 18px;
}

.inference-field {
  display: grid;
  gap: 10px;
  min-width: 0;
  color: #111827;
  font-size: 14px;
}

.field-title {
  display: block;
  line-height: 20px;
}

.inference-field.required .field-title::before {
  content: "* ";
  color: #f04438;
}

.field-control {
  display: block;
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  height: 34px;
  border: 1px solid #d0d5dd;
  border-radius: 2px;
  background: #fff;
  color: #111827;
  padding: 0 12px;
  font-size: 14px;
  line-height: 32px;
}

.field-select {
  appearance: none;
  background-image:
    linear-gradient(45deg, transparent 50%, #667085 50%),
    linear-gradient(135deg, #667085 50%, transparent 50%);
  background-position:
    calc(100% - 16px) 14px,
    calc(100% - 11px) 14px;
  background-repeat: no-repeat;
  background-size: 5px 5px, 5px 5px;
  padding-right: 32px;
}

.field-control::placeholder {
  color: #98a2b3;
}

.field-control:focus {
  border-color: #1763ff;
  outline: 1px solid #1763ff;
}

.experience-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 36px;
}

.experience-actions button,
.result-header button {
  border-radius: 4px;
  cursor: pointer;
}

.primary-action {
  border: 1px solid #1763ff;
  background: #1763ff;
  color: #fff;
  padding: 10px;
}

.primary-action:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.secondary-action {
  border: 1px solid #d0d5dd;
  background: #fff;
  color: #344054;
  padding: 10px;
}

.experience-error {
  margin-top: 12px;
  color: #f04438;
}

.result-panel {
  min-width: 0;
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #edf0f5;
  padding-bottom: 16px;
}

.result-header div {
  display: inline-flex;
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  overflow: hidden;
}

.result-header button {
  border: 0;
  background: #fff;
  padding: 8px 16px;
}

.result-header button.active {
  background: #eff6ff;
  color: #1763ff;
}

.result-body {
  display: grid;
  min-height: 420px;
  place-items: center;
  padding: 28px;
}

.result-body img {
  max-width: 100%;
  max-height: 520px;
  object-fit: contain;
}

.result-body pre {
  width: 100%;
  min-height: 360px;
  overflow: auto;
  border: 1px solid #dfe5ef;
  background: #f6f8fc;
  padding: 20px;
  font-family: Consolas, monospace;
  line-height: 1.6;
}

.empty-result {
  display: grid;
  gap: 18px;
  justify-items: center;
  color: #111827;
  text-align: center;
}

.empty-result strong {
  font-size: 22px;
}

.empty-result span {
  color: #111827;
}

@media (max-width: 1100px) {
  .experience-panel {
    grid-template-columns: 1fr;
  }

  .test-images {
    border-right: 0;
    border-bottom: 1px solid #98a2b3;
    padding-right: 0;
    padding-bottom: 22px;
  }
}
</style>
