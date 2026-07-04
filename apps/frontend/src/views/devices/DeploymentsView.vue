<template>
  <div class="panel">
    <div class="toolbar">
      <h2 class="section-title">部署记录</h2>
      <div class="toolbar-right">
        <el-button @click="refresh">刷新</el-button>
        <el-button type="primary" @click="createDeployment">创建部署</el-button>
      </div>
    </div>

    <el-form :inline="true" :model="form" class="section">
      <el-form-item label="设备 ID">
        <el-input v-model="form.device_id" />
      </el-form-item>
      <el-form-item label="应用版本 ID">
        <el-input v-model="form.edge_app_version_id" />
      </el-form-item>
    </el-form>

    <el-alert v-if="error" :title="error" type="error" show-icon class="section" />
    <el-table v-loading="loading" :data="deployments" row-key="id" empty-text="暂无部署">
      <el-table-column prop="id" label="部署 ID" min-width="240" show-overflow-tooltip />
      <el-table-column prop="device_id" label="设备 ID" min-width="220" show-overflow-tooltip />
      <el-table-column prop="edge_app_version_id" label="版本 ID" min-width="220" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="130" />
      <el-table-column label="激活" width="90">
        <template #default="{ row }">
          <el-tag :type="row.active ? 'success' : 'info'">{{ row.active ? "是" : "否" }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :disabled="row.status !== 'running'" @click="api.stopDeployment(row.id).then(refresh)">停止</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";

import { api, type DeploymentRecord } from "@/api/client";

const loading = ref(false);
const error = ref("");
const deployments = ref<DeploymentRecord[]>([]);
const form = reactive({ device_id: "", edge_app_version_id: "" });

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    deployments.value = (await api.listDeployments()).items;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "部署记录加载失败";
  } finally {
    loading.value = false;
  }
}

async function createDeployment() {
  await api.createDeployment(form);
  Object.assign(form, { device_id: "", edge_app_version_id: "" });
  await refresh();
}

refresh();
</script>
