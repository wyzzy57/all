<template>
  <section class="services-view">
    <template v-if="!selectedService">
      <header class="services-header">
        <h1>服务列表</h1>
        <div class="services-tools">
          <el-select v-model="sortMode" class="sort-select">
            <el-option label="时间倒序" value="newest" />
            <el-option label="时间正序" value="oldest" />
          </el-select>
          <el-input v-model="keyword" class="search-input" placeholder="搜索">
            <template #suffix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>
      </header>

      <div class="service-grid">
        <article
          v-for="service in filteredServices"
          :key="service.id"
          class="service-card"
          role="button"
          tabindex="0"
          :data-testid="`service-card-${service.id}`"
          @click="openService(service)"
          @keydown.enter.prevent="openService(service)"
        >
          <h2>{{ service.name }}</h2>
          <time>{{ service.createdAt }}</time>
          <p>产线名称:<span>{{ service.pipelineName }}</span></p>
          <footer>
            <span class="service-status" :class="service.status">
              <el-icon><CircleCheck v-if="service.status === 'running'" /><CircleClose v-else /></el-icon>
              {{ statusText(service.status) }}
            </span>
            <button
              type="button"
              class="delete-service"
              title="删除"
              aria-label="删除服务"
              :data-testid="`delete-service-${service.id}`"
              @click.stop="deleteService(service)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M9 3h6l1 2h4v2H4V5h4l1-2Z" />
                <path d="M6 9h12l-1 11H7L6 9Zm4 2v7h2v-7h-2Zm4 0v7h2v-7h-2Z" />
              </svg>
            </button>
            <i></i>
            <button type="button" class="text-action warning" @click.stop="toggleService(service)">
              {{ service.status === "running" ? "中止" : "重启" }}
            </button>
            <i></i>
            <button type="button" class="text-action" @click.stop="openService(service, 'logs')">查看日志</button>
          </footer>
        </article>
      </div>

      <footer class="service-pagination">
        <span>共 {{ filteredServices.length }} 条</span>
        <button type="button">1</button>
        <el-select v-model="pageSize" class="page-size">
          <el-option label="20 条/页" :value="20" />
          <el-option label="40 条/页" :value="40" />
        </el-select>
      </footer>
    </template>

    <template v-else>
      <button class="back-link" type="button" @click="backToList">
        <el-icon><ArrowLeft /></el-icon>
        返回产线列表
      </button>

      <header class="detail-header">
        <div>
          <h1>{{ selectedService.name }}</h1>
          <p>所属产线：<span>{{ selectedService.pipelineName }}</span><a href="#">点击前往</a></p>
        </div>
        <div v-if="activeTab === 'experience'" class="endpoint-box">
          <button type="button"><el-icon><Setting /></el-icon>关闭数据标注</button>
          <span>{{ selectedService.endpoint }}</span>
        </div>
      </header>

      <nav class="detail-tabs">
        <button :class="{ active: activeTab === 'basic' }" type="button" @click="activeTab = 'basic'">基础信息</button>
        <button :class="{ active: activeTab === 'experience' }" type="button" @click="activeTab = 'experience'">在线体验</button>
      </nav>

      <section v-if="activeTab === 'basic'" class="basic-panel">
        <div class="status-row">
          <span>服务状态:</span>
          <strong :class="selectedService.status">{{ statusText(selectedService.status) }}</strong>
          <button class="icon-action" type="button" title="删除" aria-label="删除服务" @click="deleteService(selectedService)">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M9 3h6l1 2h4v2H4V5h4l1-2Z" />
              <path d="M6 9h12l-1 11H7L6 9Zm4 2v7h2v-7h-2Zm4 0v7h2v-7h-2Z" />
            </svg>
          </button>
        </div>
        <div class="metric-row">
          <div><strong>环境类型</strong><span>{{ selectedService.environment }}</span></div>
          <div><strong>创建时间</strong><span>{{ selectedService.createdAt }}</span></div>
          <div><strong>调用次数</strong><span>{{ selectedService.calls }}</span></div>
          <div><strong>运行时长</strong><span>{{ selectedService.duration }}</span></div>
        </div>
        <div class="detail-subtabs">
          <button :class="{ active: detailSubtab === 'example' }" type="button" @click="detailSubtab = 'example'">调用示例</button>
          <button :class="{ active: detailSubtab === 'logs' }" type="button" @click="detailSubtab = 'logs'">日志</button>
        </div>
        <pre class="code-panel">{{ detailSubtab === "example" ? selectedService.exampleCode : selectedService.logs }}</pre>
      </section>

      <section v-else class="experience-panel">
        <aside class="test-images">
          <h2>选择测试图像</h2>
          <img class="selected-test-image" :src="selectedExample.image" :alt="selectedExample.name" />
          <div class="thumb-grid">
            <button
              v-for="example in allTestExamples"
              :key="example.id"
              :class="{ active: selectedExampleId === example.id }"
              type="button"
              @click="selectedExampleId = example.id"
            >
              <img :src="example.thumb" :alt="example.name" />
            </button>
            <label class="upload-example" for="service-example-upload">
              <input
                id="service-example-upload"
                type="file"
                accept="image/jpeg,image/png,image/tiff,image/bmp"
                @change="handleExampleUpload"
              />
              <span aria-hidden="true">↥</span>
            </label>
          </div>
          <p>支持用户上传测试图像（.jpeg /.jpg /.png /.tiff /.tif /.bmp /.pdf文件格式），文件体积不超过10MB。</p>
          <div class="experience-actions">
            <button type="button" class="primary-action" :disabled="experienceRunning" @click="runExperience">
              {{ experienceRunning ? "运行中..." : "运行" }}
            </button>
            <button type="button" class="secondary-action" @click="resetExperience">重置</button>
          </div>
        </aside>
        <main class="result-panel">
          <div class="result-header">
            <span>运行结果</span>
            <div>
              <button :class="{ active: resultMode === 'image' }" type="button" @click="resultMode = 'image'">图片</button>
              <button :class="{ active: resultMode === 'json' }" type="button" @click="resultMode = 'json'">JSON</button>
            </div>
          </div>
          <div class="result-body">
            <img v-if="resultMode === 'image'" :src="resultImage" alt="运行结果" />
            <pre v-else>{{ resultJson }}</pre>
          </div>
        </main>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { ArrowLeft, CircleCheck, CircleClose, Search, Setting } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, type DeploymentServiceRecord, type PipelinePredictResponse } from "@/api/client";

type ServiceStatus = "running" | "stopped" | "deploying";
type ServiceTab = "basic" | "experience";
type DetailSubtab = "example" | "logs";
type ResultMode = "image" | "json";

type TestExample = {
  id: string;
  name: string;
  image: string;
  thumb: string;
};

type ServiceRecord = {
  id: string;
  name: string;
  pipelineName: string;
  createdAt: string;
  status: ServiceStatus;
  environment: string;
  calls: number;
  duration: string;
  endpoint: string;
  exampleCode: string;
  logs: string;
};

const route = useRoute();
const router = useRouter();

const services = ref<ServiceRecord[]>([]);

const keyword = ref("");
const sortMode = ref("newest");
const pageSize = ref(20);
const activeTab = ref<ServiceTab>("basic");
const detailSubtab = ref<DetailSubtab>("example");
const selectedExampleId = ref("anime-group");
const resultMode = ref<ResultMode>("image");
const experienceRunning = ref(false);
const inferenceResult = ref<PipelinePredictResponse | null>(null);
const uploadedExamples = ref<TestExample[]>([]);

onMounted(() => {
  void loadServices();
});

const sampleExamples: TestExample[] = [
  { id: "anime-group", name: "????", image: "/service-examples/anime-group.png", thumb: "/service-examples/anime-group.png" },
  { id: "pandas", name: "??", image: "/service-examples/pandas.png", thumb: "/service-examples/pandas.png" },
  { id: "document-flow", name: "????", image: "/service-examples/document-flow.png", thumb: "/service-examples/document-flow.png" },
  { id: "snowboard", name: "??", image: "/service-examples/snowboard.png", thumb: "/service-examples/snowboard.png" },
  { id: "cats", name: "??", image: "/service-examples/cats.png", thumb: "/service-examples/cats.png" },
  { id: "living-room", name: "??", image: "/service-examples/living-room.png", thumb: "/service-examples/living-room.png" },
  { id: "drinks", name: "??", image: "/service-examples/drinks.png", thumb: "/service-examples/drinks.png" },
  { id: "city-traffic", name: "????", image: "/service-examples/city-traffic.png", thumb: "/service-examples/city-traffic.png" },
  { id: "fruit-basket", name: "??", image: "/service-examples/fruit-basket.png", thumb: "/service-examples/fruit-basket.png" },
];

const selectedService = computed(() => {
  const id = typeof route.params.serviceId === "string" ? route.params.serviceId : "";
  return services.value.find((service) => service.id === id);
});

const filteredServices = computed(() => {
  const term = keyword.value.trim().toLowerCase();
  const rows = services.value.filter((service) =>
    !term || [service.name, service.pipelineName, service.status].join(" ").toLowerCase().includes(term),
  );
  return [...rows].sort((left, right) =>
    sortMode.value === "oldest"
      ? left.createdAt.localeCompare(right.createdAt)
      : right.createdAt.localeCompare(left.createdAt),
  );
});

const allTestExamples = computed(() => [...sampleExamples, ...uploadedExamples.value]);
const selectedExample = computed(() => allTestExamples.value.find((item) => item.id === selectedExampleId.value) ?? sampleExamples[0]);

const resultImage = computed(() => inferenceResult.value?.result_image ?? selectedExample.value.image);
const resultJson = computed(() =>
  inferenceResult.value
    ? JSON.stringify(inferenceResult.value, null, 2)
    : JSON.stringify(
        {
          service: selectedService.value?.name,
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
  () => route.params.serviceId,
  () => {
    activeTab.value = "basic";
    detailSubtab.value = "example";
    resultMode.value = "image";
    inferenceResult.value = null;
  },
);

function openService(service: ServiceRecord, subtab: DetailSubtab = "example") {
  detailSubtab.value = subtab;
  void router.push(`/services/${service.id}`);
}

function backToList() {
  void router.push("/services");
}

async function toggleService(service: ServiceRecord) {
  const nextStatus = service.status === "running" ? "stopped" : "running";
  try {
    const updated = await api.updateService(service.id, { status: nextStatus });
    service.status = updated.status;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "服务状态更新失败");
  }
}

async function deleteService(service: ServiceRecord) {
  try {
    await api.deleteService(service.id);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "服务删除失败");
    return;
  }
  services.value = services.value.filter((item) => item.id !== service.id);
  if (selectedService.value?.id === service.id) {
    backToList();
  }
}

async function loadServices() {
  try {
    const response = await api.listServices({ limit: 200, offset: 0 });
    services.value = response.items.map(serviceFromApi);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "服务列表加载失败");
  }
}

function serviceFromApi(service: DeploymentServiceRecord): ServiceRecord {
  const pipelineName = typeof service.config.pipeline_name === "string" ? service.config.pipeline_name : service.pipeline_id;
  const createdAt = service.created_at ? new Date(service.created_at).toLocaleString("zh-CN", { hour12: false }) : "-";
  return {
    id: service.id,
    name: service.name,
    pipelineName,
    createdAt,
    status: service.status,
    environment: service.resource_summary || service.environment,
    calls: service.calls,
    duration: "刚刚创建",
    endpoint: service.endpoint,
    exampleCode: `# POST ${service.endpoint}\n# model_weight=${service.model_weight}\n# instance=${service.instance_name}`,
    logs: `[INFO] ${createdAt} service ${service.name} deployed\n[INFO] environment: ${service.environment}\n[INFO] instance: ${service.instance_name}`,
  };
}

function statusText(status: ServiceStatus) {
  if (status === "running") return "运行中";
  if (status === "deploying") return "部署中";
  return "已终止";
}

async function runExperience() {
  const service = selectedService.value;
  if (!service) return;
  experienceRunning.value = true;
  try {
    const response = await fetch(selectedExample.value.image);
    if (!response.ok) throw new Error("测试图片读取失败");
    const blob = await response.blob();
    const filename = selectedExample.value.name || "test-image.png";
    inferenceResult.value = await api.predictServiceImage(service.id, new File([blob], filename, { type: blob.type || "image/png" }));
    resultMode.value = "image";
    service.calls += 1;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "服务推理失败");
  } finally {
    experienceRunning.value = false;
  }
}

function resetExperience() {
  inferenceResult.value = null;
  resultMode.value = "image";
  selectedExampleId.value = sampleExamples[0].id;
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
  inferenceResult.value = null;
  resultMode.value = "image";
  input.value = "";
}

</script>

<style scoped>
.services-view {
  min-height: 100%;
  color: #111827;
}

.services-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 48px;
}

.services-header h1,
.detail-header h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
}

.services-tools {
  display: flex;
  gap: 16px;
}

.sort-select {
  width: 120px;
}

.search-input {
  width: 280px;
}

.service-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 20px 16px;
}

.service-card {
  min-height: 150px;
  border: 1px solid #dfe5ef;
  border-radius: 4px;
  background: #fff;
  padding: 22px 22px 16px;
  cursor: pointer;
}

.service-card:hover {
  border-color: #2f7df6;
}

.service-card h2 {
  margin: 0 0 14px;
  font-size: 16px;
}

.service-card time,
.service-card p {
  color: #475467;
  font-size: 14px;
}

.service-card p {
  margin: 22px 0;
}

.service-card footer {
  display: flex;
  align-items: center;
  gap: 10px;
}

.service-card footer i {
  width: 1px;
  height: 16px;
  background: #dfe5ef;
}

.service-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: 16px;
  padding: 6px 10px;
  font-size: 13px;
}

.service-status.running {
  background: #dcfce7;
  color: #059669;
}

.service-status.deploying {
  background: #f3e8ff;
  color: #8b5cf6;
}

.service-status.stopped {
  background: #dbeafe;
  color: #1763ff;
}

.icon-action,
.delete-service,
.text-action,
.back-link,
.detail-tabs button,
.detail-subtabs button {
  border: 0;
  background: transparent;
  cursor: pointer;
}

.icon-action,
.delete-service {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  color: #667085;
  font-size: 18px;
  flex: 0 0 28px;
}

.delete-service {
  color: #475467;
  font-size: 18px;
}

.icon-action svg,
.delete-service svg {
  width: 18px;
  height: 18px;
  display: block;
  fill: currentColor;
}

.icon-action:hover,
.delete-service:hover {
  color: #111827;
}

.text-action {
  color: #1763ff;
  font-size: 14px;
}

.text-action.warning {
  color: #ff7a1a;
}

.service-pagination {
  position: fixed;
  right: 22px;
  bottom: 24px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.service-pagination button {
  border: 0;
  border-radius: 4px;
  background: #eef5ff;
  color: #1763ff;
  padding: 8px 12px;
}

.page-size {
  width: 112px;
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 46px;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 36px;
}

.detail-header p {
  margin: 18px 0 0;
  color: #667085;
}

.detail-header a {
  margin-left: 24px;
  color: #1763ff;
}

.endpoint-box {
  display: grid;
  gap: 10px;
  justify-items: end;
  color: #98a2b3;
}

.endpoint-box button {
  border: 1px solid #cfd8e6;
  background: #f8fbff;
  padding: 9px 24px;
}

.detail-tabs {
  display: flex;
  gap: 34px;
  border-bottom: 1px solid #dfe5ef;
}

.detail-tabs button {
  padding: 0 0 18px;
  color: #344054;
  font-size: 16px;
}

.detail-tabs button.active {
  border-bottom: 3px solid #1763ff;
  color: #1763ff;
  font-weight: 700;
}

.basic-panel,
.experience-panel {
  padding: 28px 40px;
}

.status-row {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-bottom: 24px;
}

.status-row strong.running {
  color: #059669;
}

.status-row strong.stopped {
  color: #1763ff;
}

.metric-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 22px 0 34px;
}

.metric-row div {
  display: grid;
  gap: 18px;
  text-align: center;
}

.metric-row div + div {
  border-left: 1px solid #dfe5ef;
}

.metric-row strong {
  font-size: 16px;
}

.metric-row span {
  color: #344054;
}

.detail-subtabs {
  display: flex;
  margin: 0 0 18px 40px;
}

.detail-subtabs button {
  min-width: 180px;
  border: 1px solid #dfe5ef;
  padding: 12px;
}

.detail-subtabs button.active {
  background: #eff6ff;
  color: #1763ff;
  font-weight: 700;
}

.code-panel {
  min-height: 360px;
  overflow: auto;
  border: 1px solid #dfe5ef;
  background: #f6f8fc;
  padding: 42px 32px;
  color: #344054;
  font-family: Consolas, monospace;
  font-size: 14px;
  line-height: 1.6;
}

.experience-panel {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 24px;
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
  width: 100%;
  aspect-ratio: 16 / 9;
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

.thumb-grid button.active {
  border-color: #1763ff;
  box-shadow: 0 0 0 1px #1763ff inset;
}

.thumb-grid img {
  display: block;
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
}

.test-images p {
  color: #667085;
  font-size: 13px;
  line-height: 1.6;
}

.experience-actions {
  display: flex;
  gap: 66px;
  margin-top: 26px;
}

.primary-action,
.secondary-action {
  min-width: 118px;
  border-radius: 4px;
  padding: 10px 26px;
}

.primary-action {
  border: 1px solid #1763ff;
  background: #1763ff;
  color: #fff;
}

.secondary-action {
  border: 1px solid #d0d5dd;
  background: #fff;
}

.result-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
}

.result-header div {
  display: flex;
}

.result-header button {
  min-width: 120px;
  border: 1px solid #dfe5ef;
  background: #fff;
  padding: 10px;
}

.result-header button.active {
  background: #eff6ff;
  color: #1763ff;
}

.result-body {
  min-height: 580px;
  border: 1px solid #dfe5ef;
  display: grid;
  place-items: center;
  overflow: auto;
}

.result-body img {
  max-height: 560px;
  max-width: 94%;
  object-fit: contain;
}

.result-body pre {
  justify-self: stretch;
  align-self: stretch;
  margin: 0;
  padding: 24px;
  background: #f6f8fc;
}

@media (max-width: 1400px) {
  .service-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
</style>
