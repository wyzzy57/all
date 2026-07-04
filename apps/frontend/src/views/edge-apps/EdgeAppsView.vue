<template>
  <div class="panel">
    <div class="toolbar">
      <h2 class="section-title">边缘应用</h2>
      <div class="toolbar-right">
        <el-button @click="refresh">刷新</el-button>
        <el-button type="primary" @click="createApp">创建应用</el-button>
      </div>
    </div>

    <el-form :inline="true" :model="form" class="section">
      <el-form-item label="名称">
        <el-input v-model="form.name" placeholder="quality-line" />
      </el-form-item>
      <el-form-item label="说明">
        <el-input v-model="form.description" placeholder="产线缺陷检测" />
      </el-form-item>
    </el-form>

    <el-alert v-if="error" :title="error" type="error" show-icon class="section" />
    <el-table v-loading="loading" :data="apps" row-key="id" empty-text="暂无边缘应用">
      <el-table-column prop="name" label="应用" min-width="180" />
      <el-table-column prop="description" label="说明" min-width="220" />
      <el-table-column prop="status" label="状态" width="120" />
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="openVersion(row)">版本</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="versionDrawer" title="应用版本" size="620px">
      <el-form :model="versionForm" label-width="110px">
        <el-form-item label="训练模型 ID">
          <el-input v-model="versionForm.trained_model_id" />
        </el-form-item>
        <el-form-item label="版本号">
          <el-input v-model="versionForm.version" placeholder="留空自动生成" />
        </el-form-item>
        <el-form-item label="导出格式">
          <el-select v-model="versionForm.export_format">
            <el-option label="ONNX" value="onnx" />
            <el-option label="TorchScript" value="torchscript" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="createVersion">创建版本包</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="versions" row-key="id" empty-text="暂无版本">
        <el-table-column prop="version" label="版本" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column prop="package_uri" label="包 URI" min-width="240" show-overflow-tooltip />
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";

import { api, type EdgeAppRecord, type EdgeAppVersionRecord } from "@/api/client";

const loading = ref(false);
const error = ref("");
const apps = ref<EdgeAppRecord[]>([]);
const versions = ref<EdgeAppVersionRecord[]>([]);
const selectedApp = ref<EdgeAppRecord | null>(null);
const versionDrawer = ref(false);
const form = reactive({ name: "", description: "" });
const versionForm = reactive({ trained_model_id: "", version: "", export_format: "onnx" });

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    apps.value = (await api.listEdgeApps()).items;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "边缘应用加载失败";
  } finally {
    loading.value = false;
  }
}

async function createApp() {
  await api.createEdgeApp(form);
  Object.assign(form, { name: "", description: "" });
  await refresh();
}

async function openVersion(app: EdgeAppRecord) {
  selectedApp.value = app;
  versionDrawer.value = true;
  versions.value = (await api.listEdgeAppVersions(app.id)).items;
}

async function createVersion() {
  if (!selectedApp.value) return;
  const payload = {
    trained_model_id: versionForm.trained_model_id,
    version: versionForm.version || undefined,
    export_format: versionForm.export_format
  };
  await api.createEdgeAppVersion(selectedApp.value.id, payload);
  versions.value = (await api.listEdgeAppVersions(selectedApp.value.id)).items;
}

refresh();
</script>
