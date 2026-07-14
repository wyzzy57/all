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
  DataAnalysis,
  Files,
  PieChart,
  Tickets,
  TrendCharts
} from "@element-plus/icons-vue";

const navItems = [
  { path: "/workbench", label: "工作台", icon: PieChart },
  { path: "/model-space", label: "模型空间", icon: Box },
  { path: "/data-preparation", label: "数据准备", icon: Files },
  { path: "/services", label: "服务列表", icon: Tickets },
  { path: "/training-visualization", label: "可视化训练", icon: TrendCharts },
  { path: "/tasks", label: "任务中心", icon: DataAnalysis }
];
</script>
