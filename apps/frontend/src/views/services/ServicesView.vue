<template>
  <section class="services-view" :class="{ 'list-mode': !selectedService }">
    <template v-if="!selectedService">
      <header class="services-header">
        <h1>服务列表</h1>
        <div class="services-tools">
          <el-select v-model="sortMode" class="sort-select">
            <el-option label="时间倒序" value="newest" />
            <el-option label="时间正序" value="oldest" />
          </el-select>
          <el-input v-model="keyword" class="search-input" placeholder="搜索">
            <template #suffix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
      </header>

      <el-empty v-if="filteredServices.length === 0" description="暂无服务" />
      <div v-else class="service-grid">
        <article
          v-for="service in pagedServices"
          :key="service.id"
          class="service-card"
          role="button"
          tabindex="0"
          :data-testid="`service-card-${service.id}`"
          @click="openService(service)"
          @keydown.enter.prevent="openService(service)"
        >
          <div class="card-heading">
            <h2>{{ service.name }}</h2>
            <span v-if="isActive(service.status)" class="live-dot" aria-label="部署任务执行中"></span>
          </div>
          <time>{{ service.createdAt }}</time>
          <p>产线名称：<span>{{ service.pipelineName }}</span></p>
          <p class="phase-line">{{ phaseText(service.phase) }} · {{ healthText(service.healthStatus) }}</p>
          <footer>
            <span class="service-status" :class="statusTone(service.status)">{{ statusText(service.status) }}</span>
            <button
              v-if="service.status === 'stopped'"
              type="button"
              class="text-action"
              :data-testid="`start-service-${service.id}`"
              @click.stop="startService(service)"
            >恢复</button>
            <button
              v-else-if="service.status === 'running'"
              type="button"
              class="text-action"
              :data-testid="`restart-service-${service.id}`"
              @click.stop="restartService(service)"
            >重启</button>
            <button
              type="button"
              class="delete-service"
              title="删除"
              aria-label="删除服务"
              :data-testid="`delete-service-${service.id}`"
              @click.stop="deleteService(service)"
            ><Delete /></button>
            <i></i>
            <button
              type="button"
              class="text-action warning"
              :disabled="!canStop(service.status)"
              :data-testid="`stop-service-${service.id}`"
              @click.stop="stopService(service)"
            >停止</button>
            <i></i>
            <button type="button" class="text-action" @click.stop="openService(service, 'logs')">查看日志</button>
          </footer>
        </article>
      </div>

      <footer class="service-pagination">
        <span>共 {{ filteredServices.length }} 条</span>
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          background
          layout="prev, pager, next, sizes"
          :page-sizes="[20, 40]"
          :total="filteredServices.length"
        />
      </footer>
    </template>

    <template v-else>
      <button class="back-link" type="button" @click="backToList"><ArrowLeft />返回产线列表</button>
      <header class="detail-header">
        <div>
          <h1>{{ selectedService.name }}</h1>
          <p>所属产线：<span>{{ selectedService.pipelineName }}</span></p>
        </div>
        <div class="detail-actions">
          <button type="button" @click="openServiceSharing(selectedService)">
            访问配置
          </button>
          <button
            type="button"
            :disabled="!canStop(selectedService.status) || actionRunning"
            :data-testid="`stop-service-${selectedService.id}`"
            @click="stopService(selectedService)"
          ><VideoPause />停止</button>
          <button
            v-if="selectedService.status === 'stopped'"
            type="button"
            :disabled="actionRunning"
            :data-testid="`start-service-${selectedService.id}`"
            @click="startService(selectedService)"
          ><VideoPlay />恢复</button>
          <button
            v-if="selectedService.status === 'running'"
            type="button"
            :disabled="actionRunning"
            :data-testid="`restart-service-${selectedService.id}`"
            @click="restartService(selectedService)"
          ><Refresh />重启</button>
          <button
            type="button"
            :disabled="actionRunning || isActive(selectedService.status)"
            :data-testid="`rollback-service-${selectedService.id}`"
            @click="rollbackService(selectedService)"
          ><RefreshLeft />回滚</button>
        </div>
      </header>

      <nav class="detail-tabs">
        <button :class="{ active: activeTab === 'basic' }" type="button" @click="activeTab = 'basic'">基础信息</button>
        <button :class="{ active: activeTab === 'experience' }" type="button" @click="activeTab = 'experience'">在线体验</button>
      </nav>

      <section v-if="activeTab === 'basic'" class="basic-panel">
        <div class="lifecycle-banner" :class="statusTone(selectedService.status)">
          <div>
            <span>服务状态</span>
            <strong>{{ statusText(selectedService.status) }}</strong>
          </div>
          <ol class="phase-track" aria-label="部署进度">
            <li
              v-for="phase in deploymentPhases"
              :key="phase.value"
              :class="{ complete: phaseIndex(selectedService.phase) >= phase.index, current: selectedService.phase === phase.value }"
            >{{ phase.label }}</li>
          </ol>
        </div>

        <div class="metric-grid">
          <div><span>健康状态</span><strong :class="healthTone(selectedService.healthStatus)">{{ healthText(selectedService.healthStatus) }}</strong></div>
          <div><span>最近检查</span><strong>{{ selectedService.healthCheckedAt }}</strong></div>
          <div><span>边缘节点</span><strong>{{ selectedService.nodeId || "-" }}</strong></div>
          <div><span>容器</span><strong>{{ shortContainerId(selectedService.containerId) }}</strong></div>
          <div><span>运行引擎</span><strong>{{ selectedService.engine || "等待部署" }}</strong></div>
          <div><span>服务端点</span><strong>{{ selectedService.endpoint || "-" }}</strong></div>
          <div><span>创建时间</span><strong>{{ selectedService.createdAt }}</strong></div>
        </div>

        <el-alert
          v-if="selectedService.errorMessage"
          type="error"
          :title="selectedService.errorMessage"
          :description="selectedService.errorCode || undefined"
          show-icon
          :closable="false"
        />

        <div class="detail-subtabs">
          <button :class="{ active: detailSubtab === 'example' }" type="button" @click="detailSubtab = 'example'">调用示例</button>
          <button :class="{ active: detailSubtab === 'logs' }" type="button" @click="openLogs">日志</button>
        </div>
        <pre v-if="detailSubtab === 'example'" class="code-panel">{{ selectedService.exampleCode }}</pre>
        <LogStreamViewer
          v-else-if="selectedService.logStreamId"
          :stream-id="selectedService.logStreamId"
        />
        <div v-else class="log-panel">
          <div class="log-toolbar">
            <span>脱敏执行日志</span>
            <button type="button" :disabled="logLoading" @click="loadServiceLog()">{{ logLoading ? "刷新中" : "刷新" }}</button>
          </div>
          <pre>{{ selectedService.logs }}</pre>
        </div>
      </section>

      <ServiceExperiencePanel
        v-else
        :service-name="selectedService.name"
        :run-inference="runServiceInference"
      />
    </template>

    <ResourceSharingDialog
      v-if="sharingService"
      v-model="sharingDialogVisible"
      resource-type="service"
      :resource-id="sharingService.id"
      :resource-name="sharingService.name"
      @saved="handleServiceSharingSaved"
    />
  </section>
</template>

<script setup lang="ts">
import { ArrowLeft, Delete, Refresh, RefreshLeft, Search, VideoPause, VideoPlay } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, type DeploymentServiceRecord } from "@/api/client";
import ServiceExperiencePanel, {
  type ExperienceInferenceRequest,
  type ExperienceInferenceResponse,
} from "@/components/ServiceExperiencePanel.vue";
import LogStreamViewer from "@/components/logs/LogStreamViewer.vue";
import ResourceSharingDialog from "@/components/sharing/ResourceSharingDialog.vue";

type ServiceTab = "basic" | "experience";
type DetailSubtab = "example" | "logs";

type ServiceRecord = {
  id: string;
  name: string;
  pipelineName: string;
  createdAt: string;
  status: string;
  environment: string;
  calls: number;
  endpoint: string;
  exampleCode: string;
  logs: string;
  nodeId: string;
  containerId: string;
  engine: string;
  healthStatus: string;
  healthCheckedAt: string;
  phase: string;
  logUri: string;
  logStreamId: string;
  desiredState: string;
  activeRevision: number | null;
  errorCode: string;
  errorMessage: string;
  visibility: string;
};

const route = useRoute();
const router = useRouter();
const services = ref<ServiceRecord[]>([]);
const keyword = ref("");
const sortMode = ref("newest");
const currentPage = ref(1);
const pageSize = ref(20);
const activeTab = ref<ServiceTab>("basic");
const detailSubtab = ref<DetailSubtab>("example");
const actionRunning = ref(false);
const logLoading = ref(false);
const sharingDialogVisible = ref(false);
const sharingService = ref<ServiceRecord | null>(null);
let pollTimer: number | undefined;

const deploymentPhases = [
  { value: "queued", label: "排队", index: 0 },
  { value: "connecting", label: "连接", index: 1 },
  { value: "probing", label: "探测", index: 2 },
  { value: "preparing", label: "准备", index: 3 },
  { value: "optimizing", label: "优化", index: 4 },
  { value: "starting", label: "启动", index: 5 },
  { value: "warming_up", label: "预热", index: 6 },
  { value: "running", label: "运行", index: 7 },
];

const selectedService = computed(() => {
  const id = typeof route.params.serviceId === "string" ? route.params.serviceId : "";
  return services.value.find((service) => service.id === id);
});

const filteredServices = computed(() => {
  const term = keyword.value.trim().toLowerCase();
  const rows = services.value.filter((service) =>
    !term || [service.name, service.pipelineName, service.status, service.phase].join(" ").toLowerCase().includes(term),
  );
  return [...rows].sort((left, right) =>
    sortMode.value === "oldest" ? left.createdAt.localeCompare(right.createdAt) : right.createdAt.localeCompare(left.createdAt),
  );
});

const pagedServices = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredServices.value.slice(start, start + pageSize.value);
});

onMounted(() => {
  void loadServices();
  pollTimer = window.setInterval(pollActiveServices, 2500);
});

onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
});

watch(
  () => route.params.serviceId,
  () => {
    activeTab.value = "basic";
    detailSubtab.value = "example";
  },
);

watch([filteredServices, pageSize], () => {
  const maxPage = Math.max(1, Math.ceil(filteredServices.value.length / pageSize.value));
  if (currentPage.value > maxPage) currentPage.value = maxPage;
});

function openService(service: ServiceRecord, subtab: DetailSubtab = "example") {
  detailSubtab.value = subtab;
  void router.push(`/services/${service.id}`);
  if (subtab === "logs") void loadServiceLog(service);
}

function backToList() {
  void router.push("/services");
}

function openServiceSharing(service: ServiceRecord) {
  sharingService.value = service;
  sharingDialogVisible.value = true;
}

function handleServiceSharingSaved(visibility: string) {
  if (!sharingService.value) return;
  sharingService.value.visibility = visibility;
}

async function loadServices() {
  try {
    const response = await api.listServices({ limit: 200, offset: 0 });
    services.value = response.items.map(serviceFromApi);
  } catch (error) {
    ElMessage.error(errorMessage(error, "服务列表加载失败"));
  }
}

async function pollActiveServices() {
  const targets = services.value.filter((service) => service.status === "running" || isActive(service.status));
  await Promise.all(
    targets.map(async (service) => {
      try {
        mergeService(await api.getService(service.id));
      } catch {
        // A transient polling failure must not interrupt the next refresh cycle.
      }
    }),
  );
}

function mergeService(record: DeploymentServiceRecord) {
  const next = serviceFromApi(record);
  const index = services.value.findIndex((service) => service.id === next.id);
  if (index >= 0) {
    const previous = services.value[index];
    if (previous.logUri === next.logUri && previous.logs !== "正在读取日志...") next.logs = previous.logs;
    services.value[index] = next;
  }
  else services.value.push(next);
}

async function stopService(service: ServiceRecord) {
  actionRunning.value = true;
  try {
    mergeService(await api.stopService(service.id));
    ElMessage.success("停止任务已提交");
  } catch (error) {
    ElMessage.error(errorMessage(error, "停止服务失败"));
  } finally {
    actionRunning.value = false;
  }
}

async function startService(service: ServiceRecord) {
  actionRunning.value = true;
  try {
    mergeService(await api.startService(service.id));
    ElMessage.success("恢复任务已提交");
  } catch (error) {
    ElMessage.error(errorMessage(error, "恢复服务失败"));
  } finally {
    actionRunning.value = false;
  }
}

async function restartService(service: ServiceRecord) {
  actionRunning.value = true;
  try {
    mergeService(await api.restartService(service.id));
    ElMessage.success("重启任务已提交");
  } catch (error) {
    ElMessage.error(errorMessage(error, "重启服务失败"));
  } finally {
    actionRunning.value = false;
  }
}

async function rollbackService(service: ServiceRecord) {
  actionRunning.value = true;
  try {
    mergeService(await api.rollbackService(service.id));
    ElMessage.success("回滚任务已提交");
  } catch (error) {
    ElMessage.error(errorMessage(error, "回滚服务失败"));
  } finally {
    actionRunning.value = false;
  }
}

async function deleteService(service: ServiceRecord) {
  try {
    await api.deleteService(service.id);
    services.value = services.value.filter((item) => item.id !== service.id);
    if (selectedService.value?.id === service.id) backToList();
  } catch (error) {
    ElMessage.error(errorMessage(error, "服务删除失败"));
  }
}

function openLogs() {
  detailSubtab.value = "logs";
  void loadServiceLog();
}

async function loadServiceLog(service = selectedService.value) {
  if (!service) return;
  if (!service.logUri) {
    service.logs = service.errorMessage || `${phaseText(service.phase)}，暂未生成执行日志。`;
    return;
  }
  logLoading.value = true;
  try {
    service.logs = await api.readServiceLog(service.logUri);
  } catch (error) {
    service.logs = errorMessage(error, "日志读取失败");
  } finally {
    logLoading.value = false;
  }
}

async function runServiceInference(request: ExperienceInferenceRequest): Promise<ExperienceInferenceResponse> {
  const service = selectedService.value;
  if (!service) throw new Error("服务不存在");
  const result = await api.predictServiceImage(service.id, request.file);
  service.calls += 1;
  return result;
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
    endpoint: service.endpoint,
    exampleCode: `curl -X POST "${service.endpoint || `/services/${service.id}/predict/image`}" \\\n  -F "file=@test.jpg"`,
    logs: "正在读取日志...",
    nodeId: service.node_id || "",
    containerId: service.container_id || "",
    engine: service.engine || "",
    healthStatus: service.health_status || (service.instance_id ? "pending" : "unmanaged"),
    healthCheckedAt: service.health_checked_at
      ? new Date(service.health_checked_at).toLocaleString("zh-CN", { hour12: false })
      : "尚未检查",
    phase: service.phase || service.status,
    logUri: service.log_uri || "",
    logStreamId: service.log_stream_id || "",
    desiredState: service.desired_state || "running",
    activeRevision: service.active_revision ?? null,
    errorCode: service.error_code || "",
    errorMessage: service.error_message || "",
    visibility: service.visibility || "private",
  };
}

function isActive(status: string) {
  return ["queued", "deploying", "connecting", "probing", "preparing", "optimizing", "starting", "restarting", "warming_up", "reconciliation_retry", "upgrade_queued", "stopping", "rollback_queued"].includes(status);
}

function canStop(status: string) {
  return status === "running" || isActive(status);
}

function statusText(status: string) {
  const labels: Record<string, string> = {
    queued: "部署排队中", deploying: "部署中", connecting: "连接中", probing: "环境探测中",
    preparing: "准备资源中", optimizing: "模型优化中", starting: "启动中", warming_up: "预热中",
    running: "运行中", reconciliation_retry: "健康检查重试中", stopping: "停止排队中", stopped: "已停止", rollback_queued: "回滚排队中",
    upgrade_queued: "升级排队中", failed: "运行失败",
  };
  return labels[status] || status || "未知";
}

function phaseText(phase: string) {
  return statusText(phase);
}

function healthText(health: string) {
  const labels: Record<string, string> = { pending: "等待检查", unknown: "检查重试中", unmanaged: "无部署实例", starting: "启动中", healthy: "健康", unhealthy: "异常", stopped: "已停止" };
  return labels[health] || health || "未知";
}

function statusTone(status: string) {
  if (status === "running") return "success";
  if (status === "failed") return "danger";
  if (status === "stopped") return "neutral";
  return "progress";
}

function healthTone(health: string) {
  return health === "healthy" ? "healthy" : health === "unhealthy" ? "unhealthy" : "pending";
}

function phaseIndex(phase: string) {
  return deploymentPhases.find((item) => item.value === phase)?.index ?? -1;
}

function shortContainerId(id: string) {
  return id ? id.slice(0, 14) : "-";
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
</script>

<style scoped>
.services-view { container: services / inline-size; min-width: 0; min-height: 100%; color: #111827; }
.services-view.list-mode { display: flex; flex-direction: column; }
.services-header, .detail-header, .card-heading, .detail-actions, .service-card footer, .back-link, .log-toolbar { display: flex; align-items: center; }
.services-header, .detail-header, .log-toolbar { justify-content: space-between; }
.services-header { margin-bottom: 32px; }
.services-header h1, .detail-header h1 { margin: 0; font-size: 24px; }
.services-tools, .detail-actions { display: flex; gap: 12px; }
.sort-select { width: 130px; } .search-input { width: 280px; }
.service-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.service-card { min-width: 0; min-height: 176px; border: 1px solid var(--visiox-card-border); border-radius: var(--visiox-card-radius); background: var(--visiox-card-surface); padding: 18px; box-shadow: none; cursor: pointer; transition: border-color .15s ease; }
.service-card:hover { border-color: #aeb7c3; box-shadow: none; transform: none; }
.card-heading { justify-content: space-between; gap: 12px; }
.service-card h2 { margin: 0; font-size: 16px; }
.service-card time, .service-card p { color: #667085; font-size: 13px; }
.service-card p { margin: 18px 0 8px; } .service-card .phase-line { margin: 0 0 18px; }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #1763ff; box-shadow: 0 0 0 4px #e8f0ff; }
.service-card footer { gap: 9px; }
.service-card footer i { width: 1px; height: 16px; background: #dfe5ef; }
.service-status { border-radius: 14px; padding: 5px 9px; font-size: 12px; }
.service-status.success, .lifecycle-banner.success { background: #ecfdf3; color: #067647; }
.service-status.progress, .lifecycle-banner.progress { background: #f4f0ff; color: #6941c6; }
.service-status.danger, .lifecycle-banner.danger { background: #fef3f2; color: #b42318; }
.service-status.neutral, .lifecycle-banner.neutral { background: #f2f4f7; color: #475467; }
button { font: inherit; }
.delete-service, .text-action, .back-link { border: 0; background: transparent; cursor: pointer; }
.delete-service { width: 24px; height: 24px; padding: 3px; color: #667085; }
.delete-service :deep(svg) { width: 17px; }
.text-action { color: #1763ff; } .text-action.warning { color: #d92d20; }
.text-action:disabled { color: #b7c0ce; cursor: not-allowed; }
.service-pagination { position: sticky; bottom: 0; z-index: 5; display: flex; flex: 0 0 auto; justify-content: flex-end; align-items: center; gap: 14px; margin-top: auto; padding: 20px 0 4px; background: #fff; }
.service-pagination :deep(.el-pagination.is-background .el-pager li),
.service-pagination :deep(.el-pagination.is-background .btn-prev),
.service-pagination :deep(.el-pagination.is-background .btn-next) { background: #fff; color: #344054; }
.service-pagination :deep(.el-pagination.is-background .el-pager li.is-active) { border: 1px solid #e6e9ef; background: #f5f7fa; color: #5b9cf6; font-weight: 500; }
.back-link { gap: 6px; margin-bottom: 28px; color: #344054; }
.back-link :deep(svg) { width: 16px; }
.detail-header { margin-bottom: 28px; }
.detail-header p { color: #667085; }
.detail-actions button { display: inline-flex; align-items: center; gap: 6px; border: 1px solid #d0d5dd; border-radius: 4px; background: #fff; padding: 8px 14px; cursor: pointer; }
.detail-actions button:disabled { color: #98a2b3; cursor: not-allowed; }
.detail-actions :deep(svg) { width: 16px; }
.detail-tabs { display: flex; gap: 32px; border-bottom: 1px solid #dfe5ef; }
.detail-tabs button { border: 0; border-bottom: 3px solid transparent; background: transparent; padding: 0 0 14px; cursor: pointer; }
.detail-tabs button.active { border-bottom-color: #1763ff; color: #1763ff; font-weight: 700; }
.basic-panel { padding: 28px 0; }
.lifecycle-banner { display: grid; gap: 20px; border-radius: 4px; padding: 18px 22px; }
.lifecycle-banner > div { display: flex; align-items: baseline; gap: 16px; }
.lifecycle-banner strong { font-size: 18px; }
.phase-track { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 0; margin: 0; padding: 0; list-style: none; }
.phase-track li { position: relative; border-top: 2px solid #d0d5dd; color: #98a2b3; padding-top: 9px; font-size: 12px; text-align: center; }
.phase-track li.complete { border-color: #1763ff; color: #1763ff; }
.phase-track li.current { font-weight: 700; }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid #e4e7ec; margin: 22px 0; }
.metric-grid div { display: grid; gap: 8px; min-height: 78px; border-right: 1px solid #e4e7ec; border-bottom: 1px solid #e4e7ec; padding: 14px 18px; }
.metric-grid span { color: #667085; font-size: 13px; }
.metric-grid strong { overflow-wrap: anywhere; font-size: 14px; }
.healthy { color: #067647; } .unhealthy { color: #b42318; } .pending { color: #6941c6; }
.detail-subtabs { display: flex; margin-top: 28px; }
.detail-subtabs button { min-width: 160px; border: 1px solid #dfe5ef; background: #fff; padding: 11px; cursor: pointer; }
.detail-subtabs button.active { background: #eff6ff; color: #1763ff; font-weight: 700; }
.code-panel, .log-panel { min-height: 330px; border: 1px solid #dfe5ef; background: #f7f9fc; }
.code-panel, .log-panel pre { margin: 0; padding: 28px; color: #344054; font-family: Consolas, monospace; line-height: 1.65; white-space: pre-wrap; }
.log-toolbar { border-bottom: 1px solid #dfe5ef; padding: 12px 18px; }
.log-toolbar button { border: 0; background: transparent; color: #1763ff; cursor: pointer; }
@container services (max-width: 1240px) { .service-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@container services (max-width: 860px) {
  .services-header { align-items: flex-start; flex-direction: column; gap: 14px; margin-bottom: 22px; }
  .services-tools { width: 100%; }
  .search-input { flex: 1; width: auto; }
  .service-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .phase-track { overflow-x: auto; grid-template-columns: repeat(8, 90px); }
}
@container services (max-width: 520px) {
  .services-tools, .detail-header, .detail-actions { align-items: stretch; flex-direction: column; }
  .sort-select, .search-input { width: 100%; }
  .service-grid, .metric-grid { grid-template-columns: 1fr; }
  .service-card footer { flex-wrap: wrap; }
  .service-status, .service-card footer button { flex: none; white-space: nowrap; }
  .service-card footer i { display: none; }
  .detail-tabs { gap: 20px; overflow-x: auto; }
}
</style>
