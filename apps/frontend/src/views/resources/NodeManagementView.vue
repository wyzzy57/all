<template>
  <section class="node-page">
    <header class="page-header">
      <div><h1>节点与资源</h1><p>统一管理训练和推理使用的服务器、GPU 与兼容资源池。</p></div>
      <el-button type="primary" data-testid="onboard-node" @click="onboardingVisible = true">接入节点</el-button>
    </header>

    <div class="summary-strip">
      <div><span>节点总数</span><strong>{{ nodes.length }}</strong></div>
      <div><span>在线</span><strong>{{ onlineCount }}</strong></div>
      <div><span>GPU</span><strong>{{ gpuCount }}</strong></div>
      <div><span>资源池</span><strong>{{ pools.length }}</strong></div>
    </div>

    <el-empty v-if="!loading && !nodes.length" description="尚未接入计算节点" />
    <div v-else v-loading="loading" class="node-list">
      <article v-for="node in nodes" :key="node.id" class="node-row">
        <div class="node-heading">
          <div class="node-identity">
            <span class="status-dot" :class="statusTone(node)"></span>
            <div><h2>{{ node.name }}</h2><p>{{ node.architecture }} · {{ platformLabel(node.platform_kind) }} · {{ node.connection_method.toUpperCase() }}</p></div>
          </div>
          <div class="node-actions">
            <el-button link type="primary" :data-testid="`refresh-node-${node.id}`" :loading="busyId === node.id" @click="refresh(node)">刷新</el-button>
            <el-button link :type="node.enabled ? 'warning' : 'success'" @click="toggle(node)">{{ node.enabled ? "禁用" : "启用" }}</el-button>
            <el-button link type="danger" @click="remove(node)">删除</el-button>
          </div>
        </div>

        <div class="node-meta">
          <el-tag :type="statusTag(node.status)" effect="light">{{ statusLabel(node.status) }}</el-tag>
          <span>资源版本 {{ node.resource_revision }}</span>
          <span>最近刷新 {{ formatTime(node.inventory_refreshed_at) }}</span>
          <el-select :model-value="node.resource_pool_id" size="small" placeholder="选择资源池" @change="(value: string) => assignPool(node, value)">
            <el-option v-for="pool in pools" :key="pool.id" :label="pool.name" :value="pool.id" />
          </el-select>
        </div>
        <NodeResourcePanel :node="node" />
        <div class="runtime-line">
          <span>Docker {{ dockerVersion(node) }}</span><span>CUDA {{ text(node.fingerprint.cuda_runtime_version) }}</span><span>TensorRT {{ text(node.fingerprint.tensorrt_version) }}</span>
        </div>
      </article>
    </div>

    <NodeOnboardingDialog v-model:visible="onboardingVisible" :pools="pools" @created="load" />
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

import { api, type ComputeNodeRecord, type ResourcePoolRecord } from "@/api/client";
import NodeOnboardingDialog from "@/components/resources/NodeOnboardingDialog.vue";
import NodeResourcePanel from "@/components/resources/NodeResourcePanel.vue";

const nodes = ref<ComputeNodeRecord[]>([]);
const pools = ref<ResourcePoolRecord[]>([]);
const loading = ref(false);
const busyId = ref("");
const onboardingVisible = ref(false);
const onlineCount = computed(() => nodes.value.filter((node) => node.status === "online" && node.enabled).length);
const gpuCount = computed(() => nodes.value.reduce((sum, node) => sum + Number(node.resources.gpu_count ?? 0), 0));

onMounted(load);
async function load() {
  loading.value = true;
  try {
    const [nodeResponse, poolResponse] = await Promise.all([api.listNodes(), api.listResourcePools()]);
    nodes.value = nodeResponse.items; pools.value = poolResponse.items;
  } catch (error) { ElMessage.error(message(error, "节点资源加载失败")); }
  finally { loading.value = false; }
}
async function refresh(node: ComputeNodeRecord) {
  busyId.value = node.id;
  try { await api.refreshNode(node.id); await load(); ElMessage.success("资源已刷新"); }
  catch (error) { ElMessage.error(message(error, "资源刷新失败")); }
  finally { busyId.value = ""; }
}
async function toggle(node: ComputeNodeRecord) {
  try { if (node.enabled) await api.disableNode(node.id); else await api.enableNode(node.id); await load(); }
  catch (error) { ElMessage.error(message(error, "节点状态更新失败")); }
}
async function assignPool(node: ComputeNodeRecord, poolId: string) {
  try { await api.assignNodePool(node.id, poolId); await load(); ElMessage.success("资源池已更新"); }
  catch (error) { ElMessage.error(message(error, "资源池分配失败")); }
}
async function remove(node: ComputeNodeRecord) {
  try { await ElMessageBox.confirm(`确定删除节点“${node.name}”吗？`, "删除节点", { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }); }
  catch { return; }
  try { await api.deleteNode(node.id); await load(); }
  catch (error) { ElMessage.error(message(error, "节点删除失败")); }
}
function statusTone(node: ComputeNodeRecord) { return node.enabled && node.status === "online" ? "online" : node.status === "incompatible" ? "warning" : "offline"; }
function statusTag(status: string) { return status === "online" ? "success" : status === "incompatible" ? "warning" : "info"; }
function statusLabel(status: string) { return ({ online: "在线", offline: "离线", disabled: "已禁用", draining: "排空中", incompatible: "不兼容" } as Record<string, string>)[status] ?? status; }
function platformLabel(value: string) { return ({ x86_nvidia: "x86 NVIDIA", jetson: "NVIDIA Jetson" } as Record<string, string>)[value] ?? value; }
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "尚未刷新"; }
function dockerVersion(node: ComputeNodeRecord) { const docker = node.capabilities.docker as Record<string, unknown> | undefined; return text(docker?.version); }
function text(value: unknown) { return typeof value === "string" && value ? value : "-"; }
function message(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback; }
</script>

<style scoped>
.node-page { min-height: 100%; padding: 24px 28px 40px; background: #fff; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 22px; }
.page-header h1 { margin: 0; color: #101828; font-size: 24px; }.page-header p { margin: 7px 0 0; color: #667085; }
.summary-strip { display: grid; grid-template-columns: repeat(4, minmax(130px, 1fr)); border: 1px solid #e4e8f0; margin-bottom: 20px; }
.summary-strip div { padding: 18px 22px; border-right: 1px solid #e4e8f0; }.summary-strip div:last-child { border: 0; }
.summary-strip span { display: block; color: #667085; font-size: 13px; }.summary-strip strong { display: block; margin-top: 5px; color: #172033; font-size: 24px; }
.node-list { display: grid; gap: 14px; }.node-row { border: 1px solid #dde3ed; padding: 20px 22px; }
.node-heading, .node-identity, .node-actions, .node-meta, .runtime-line { display: flex; align-items: center; }
.node-heading { justify-content: space-between; gap: 20px; }.node-identity { gap: 12px; min-width: 0; }
.node-identity h2 { margin: 0; font-size: 17px; }.node-identity p { margin: 5px 0 0; color: #667085; font-size: 13px; }
.status-dot { width: 9px; height: 9px; border-radius: 50%; background: #98a2b3; }.status-dot.online { background: #12b76a; }.status-dot.warning { background: #f79009; }
.node-meta { gap: 16px; margin: 16px 0 18px; color: #667085; font-size: 13px; }.node-meta .el-select { width: min(320px, 32vw); margin-left: auto; }
.runtime-line { gap: 24px; margin-top: 16px; padding-top: 14px; border-top: 1px solid #edf0f5; color: #667085; font-size: 12px; }
@media (max-width: 800px) { .summary-strip { grid-template-columns: repeat(2, 1fr); }.node-heading { align-items: flex-start; }.node-meta { flex-wrap: wrap; }.node-meta .el-select { width: 100%; margin-left: 0; } }
</style>
