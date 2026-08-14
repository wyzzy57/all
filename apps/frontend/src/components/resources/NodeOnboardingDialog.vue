<template>
  <el-dialog :model-value="visible" title="接入计算节点" width="680px" destroy-on-close @close="close">
    <el-steps :active="step" finish-status="success" align-center class="onboarding-steps">
      <el-step title="连接信息" /><el-step title="确认指纹" /><el-step title="接入与探测" />
    </el-steps>

    <el-form v-if="step === 0" label-position="top" class="onboarding-form">
      <div class="form-grid">
        <el-form-item label="节点名称" required><el-input v-model="form.name" placeholder="例如 gpu-server-01" /></el-form-item>
        <el-form-item label="主机地址" required><el-input v-model="form.host" placeholder="IP 或域名" /></el-form-item>
        <el-form-item label="SSH 端口"><el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" /></el-form-item>
        <el-form-item label="管理员账号" required><el-input v-model="form.administrator" autocomplete="username" /></el-form-item>
      </div>
      <el-form-item label="临时密码" required>
        <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
        <span class="field-help">仅用于首次安装受控 SSH 密钥，平台不会保存该密码。</span>
      </el-form-item>
    </el-form>

    <section v-else-if="step === 1" class="fingerprint-panel">
      <el-alert title="请在服务器终端核对以下 SSH 主机指纹" type="warning" :closable="false" show-icon />
      <dl><dt>主机</dt><dd>{{ form.host }}:{{ form.port }}</dd><dt>密钥类型</dt><dd>{{ hostKey?.host_key_type }}</dd><dt>SHA-256 指纹</dt><dd class="fingerprint">{{ hostKey?.fingerprint }}</dd></dl>
      <el-checkbox v-model="fingerprintConfirmed">我已通过可信渠道核对并确认该指纹</el-checkbox>
    </section>

    <section v-else class="probe-panel">
      <el-icon class="probe-icon" :class="{ spinning: submitting }"><Loading /></el-icon>
      <h3>{{ submitting ? "正在安装密钥并探测资源" : "准备接入节点" }}</h3>
      <p>系统将自动识别 CPU、内存、磁盘、GPU、Docker、CUDA 与 TensorRT。</p>
      <el-form-item label="资源池（可选）">
        <el-select v-model="form.resourcePoolId" clearable placeholder="自动匹配兼容资源池">
          <el-option v-for="pool in pools" :key="pool.id" :label="pool.name" :value="pool.id" />
        </el-select>
      </el-form-item>
    </section>

    <template #footer>
      <el-button v-if="step > 0 && !submitting" @click="step -= 1">上一步</el-button>
      <el-button @click="close">取消</el-button>
      <el-button v-if="step < 2" type="primary" :loading="scanning" @click="next">下一步</el-button>
      <el-button v-else type="primary" :loading="submitting" @click="submit">确认接入</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { Loading } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { reactive, ref } from "vue";

import { api, type ResourcePoolRecord, type SshHostKeyRecord } from "@/api/client";

defineProps<{ visible: boolean; pools: ResourcePoolRecord[] }>();
const emit = defineEmits<{ "update:visible": [value: boolean]; created: [] }>();
const step = ref(0);
const scanning = ref(false);
const submitting = ref(false);
const fingerprintConfirmed = ref(false);
const hostKey = ref<SshHostKeyRecord | null>(null);
const form = reactive({ name: "", host: "", port: 22, administrator: "", password: "", resourcePoolId: "" });

function reset() {
  step.value = 0; fingerprintConfirmed.value = false; hostKey.value = null;
  Object.assign(form, { name: "", host: "", port: 22, administrator: "", password: "", resourcePoolId: "" });
}
function close() { emit("update:visible", false); reset(); }
async function next() {
  if (step.value === 0) {
    if (![form.name, form.host, form.administrator, form.password].every((value) => value.trim())) {
      ElMessage.warning("请完整填写节点连接信息"); return;
    }
    scanning.value = true;
    try { hostKey.value = await api.scanNodeHostKey({ host: form.host.trim(), port: form.port }); step.value = 1; }
    catch (error) { ElMessage.error(message(error, "主机指纹扫描失败")); }
    finally { scanning.value = false; }
    return;
  }
  if (!fingerprintConfirmed.value) { ElMessage.warning("请先确认 SSH 主机指纹"); return; }
  step.value = 2;
}
async function submit() {
  if (!hostKey.value) return;
  submitting.value = true;
  try {
    await api.createManualNode({
      name: form.name.trim(), host: form.host.trim(), port: form.port,
      administrator: form.administrator.trim(), password: form.password,
      confirmed_fingerprint: hostKey.value.fingerprint, labels: {},
      resource_pool_id: form.resourcePoolId || undefined,
    });
    ElMessage.success("节点已接入并完成资源探测"); emit("created"); close();
  } catch (error) { ElMessage.error(message(error, "节点接入失败")); }
  finally { submitting.value = false; form.password = ""; }
}
function message(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback; }
</script>

<style scoped>
.onboarding-steps { margin: 4px 0 28px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; }
.field-help { margin-top: 6px; color: #7b8496; font-size: 12px; }
.fingerprint-panel, .probe-panel { min-height: 250px; padding: 12px 8px; }
.fingerprint-panel dl { display: grid; grid-template-columns: 100px 1fr; gap: 14px; margin: 24px 0; }
.fingerprint-panel dt { color: #667085; }.fingerprint-panel dd { margin: 0; }
.fingerprint { overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.probe-panel { text-align: center; }.probe-panel .el-form-item { max-width: 420px; margin: 24px auto 0; text-align: left; }
.probe-icon { margin-top: 22px; color: #2f7cf6; font-size: 38px; }.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media not all { .form-grid { grid-template-columns: 1fr; } }
</style>
