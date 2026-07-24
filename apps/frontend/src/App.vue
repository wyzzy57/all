<template>
  <el-container class="app-shell" direction="vertical">
    <el-header class="app-header">
      <a class="brand" href="/workbench" aria-label="VisioX 首页" @click.prevent="$router.push('/workbench')">
        <span class="brand-word">Visio</span><span class="brand-x">X</span>
      </a>
      <div class="header-actions">
        <span class="environment-status"><i></i>平台服务正常</span>
        <el-tag type="success" effect="plain" round>内网部署</el-tag>
      </div>
    </el-header>

    <el-container class="app-body">
      <el-aside
        :width="sidebarWidth"
        class="app-sidebar"
        :class="{ collapsed: sidebarCollapsed }"
      >
        <nav class="sidebar-nav" aria-label="主导航">
          <div v-for="(group, groupIndex) in navGroups" :key="groupIndex" class="nav-group">
            <el-menu
              :default-active="$route.path"
              :collapse="sidebarCollapsed"
              router
              class="nav-menu"
            >
              <el-menu-item
                v-for="item in group"
                :key="item.path"
                :index="item.path"
                :aria-label="item.label"
                :title="item.label"
              >
                <el-icon><component :is="item.icon" /></el-icon>
                <span>{{ item.label }}</span>
              </el-menu-item>
            </el-menu>
          </div>
        </nav>

        <button
          class="sidebar-toggle"
          type="button"
          :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
          :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
          @click="toggleSidebar"
        >
          <el-icon><ArrowRight v-if="sidebarCollapsed" /><ArrowLeft v-else /></el-icon>
        </button>
      </el-aside>

      <el-container class="app-content-shell">
        <el-main class="app-main">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import {
  ArrowLeft,
  ArrowRight,
  Box,
  DataAnalysis,
  Files,
  PieChart,
  Tickets,
  TrendCharts,
} from "@element-plus/icons-vue";
import { computed, ref } from "vue";

const sidebarCollapsed = ref(window.localStorage.getItem("visiox.sidebar.collapsed") === "true");
const sidebarWidth = computed(() => (sidebarCollapsed.value ? "64px" : "184px"));

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value;
  window.localStorage.setItem("visiox.sidebar.collapsed", String(sidebarCollapsed.value));
}

const navGroups = [
  [
    { path: "/workbench", label: "工作台", icon: PieChart },
    { path: "/data-preparation", label: "数据准备", icon: Files },
    { path: "/model-space", label: "模型空间", icon: Box },
    { path: "/services", label: "服务列表", icon: Tickets },
  ],
  [
    { path: "/training-visualization", label: "可视化训练", icon: TrendCharts },
    { path: "/tasks", label: "任务中心", icon: DataAnalysis },
  ],
];
</script>
