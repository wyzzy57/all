<template>
  <el-container class="app-shell">
    <el-aside width="248px" class="app-sidebar">
      <div class="brand">
        <div class="brand-mark">V</div>
        <div>
          <div class="brand-name">Visiox</div>
          <div class="brand-subtitle">YOLO26 管理台</div>
        </div>
      </div>
      <el-menu :default-active="$route.path" router class="nav-menu">
        <el-menu-item v-for="item in navItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="app-header">
        <div>
          <div class="page-title">{{ currentTitle }}</div>
          <div class="page-subtitle">私有化视觉模型平台</div>
        </div>
        <el-tag type="success" effect="plain">内网部署</el-tag>
      </el-header>
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import {
  Box,
  Cpu,
  DataAnalysis,
  Files,
  Monitor,
  Operation,
  Van
} from "@element-plus/icons-vue";
import { computed } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const navItems = [
  { path: "/model-space", label: "模型空间", icon: Box },
  { path: "/data-preparation", label: "数据准备", icon: Files },
  { path: "/pipelines", label: "训练产线", icon: Operation },
  { path: "/tasks", label: "任务中心", icon: DataAnalysis },
  { path: "/devices", label: "设备与摄像头", icon: Monitor },
  { path: "/edge-apps", label: "边缘应用", icon: Cpu },
  { path: "/deployments", label: "部署记录", icon: Van }
];

const currentTitle = computed(() => {
  const match = navItems.find((item) => item.path === route.path);
  return match?.label ?? "工作台";
});
</script>
