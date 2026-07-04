<template>
  <div class="panel">
    <div class="toolbar">
      <h2 class="section-title">设备与摄像头</h2>
      <div class="toolbar-right">
        <el-button @click="refresh">刷新</el-button>
        <el-button type="primary" @click="createDevice">注册设备</el-button>
      </div>
    </div>

    <el-form :inline="true" :model="deviceForm" class="section">
      <el-form-item label="设备名">
        <el-input v-model="deviceForm.name" placeholder="edge-1" />
      </el-form-item>
      <el-form-item label="Agent 地址">
        <el-input v-model="deviceForm.endpoint_url" placeholder="http://edge-agent:8080" />
      </el-form-item>
    </el-form>

    <el-alert v-if="error" :title="error" type="error" show-icon class="section" />
    <el-table v-loading="loading" :data="devices" row-key="id" empty-text="暂无设备">
      <el-table-column prop="name" label="设备" min-width="160" />
      <el-table-column prop="endpoint_url" label="Agent 地址" min-width="260" />
      <el-table-column prop="status" label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="row.status === 'online' ? 'success' : row.status === 'error' ? 'danger' : 'info'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="290" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="check(row.id)">健康检查</el-button>
          <el-button size="small" @click="selectDevice(row)">摄像头</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="cameraDrawer" title="摄像头" size="520px">
      <el-form :model="cameraForm" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="cameraForm.name" />
        </el-form-item>
        <el-form-item label="RTSP">
          <el-input v-model="cameraForm.rtsp_url" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="createCamera">添加</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="cameras" row-key="id" empty-text="暂无摄像头">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" @click="testCamera(row.id)">测试</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import { reactive, ref } from "vue";

import { api, type CameraRecord, type DeviceRecord } from "@/api/client";

const loading = ref(false);
const error = ref("");
const devices = ref<DeviceRecord[]>([]);
const cameras = ref<CameraRecord[]>([]);
const cameraDrawer = ref(false);
const selectedDevice = ref<DeviceRecord | null>(null);
const deviceForm = reactive({ name: "", endpoint_url: "" });
const cameraForm = reactive({ name: "", rtsp_url: "" });

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    devices.value = (await api.listDevices()).items;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "设备加载失败";
  } finally {
    loading.value = false;
  }
}

async function createDevice() {
  await api.createDevice(deviceForm);
  Object.assign(deviceForm, { name: "", endpoint_url: "" });
  await refresh();
}

async function check(id: string) {
  await api.checkDevice(id);
  ElMessage.success("Agent 在线");
  await refresh();
}

async function selectDevice(device: DeviceRecord) {
  selectedDevice.value = device;
  cameraDrawer.value = true;
  cameras.value = (await api.listCameras(device.id)).items;
}

async function createCamera() {
  if (!selectedDevice.value) return;
  await api.createCamera(selectedDevice.value.id, cameraForm);
  Object.assign(cameraForm, { name: "", rtsp_url: "" });
  cameras.value = (await api.listCameras(selectedDevice.value.id)).items;
}

async function testCamera(id: string) {
  await api.testCamera(id);
  ElMessage.success("摄像头可达");
  if (selectedDevice.value) {
    cameras.value = (await api.listCameras(selectedDevice.value.id)).items;
  }
}

refresh();
</script>
