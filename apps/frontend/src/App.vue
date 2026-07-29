<template>
  <div v-if="isLoginRoute" class="auth-route">
    <router-view />
  </div>
  <el-container v-else class="app-shell" direction="vertical">
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
        :class="{ collapsed: effectiveCollapsed, 'mobile-expanded': isMobile && !effectiveCollapsed }"
      >
        <nav class="sidebar-nav" aria-label="主导航">
          <div v-for="(group, groupIndex) in navGroups" :key="groupIndex" class="nav-group">
            <el-menu
              :default-active="$route.path"
              :collapse="effectiveCollapsed"
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

        <div class="sidebar-account">
          <UserAccountMenu :collapsed="effectiveCollapsed" />
        </div>

        <button
          class="sidebar-toggle"
          type="button"
          :aria-label="effectiveCollapsed ? '展开侧边栏' : '收起侧边栏'"
          :title="effectiveCollapsed ? '展开侧边栏' : '收起侧边栏'"
          @click="toggleSidebar"
        >
          <el-icon><ArrowRight v-if="effectiveCollapsed" /><ArrowLeft v-else /></el-icon>
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
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import UserAccountMenu from "@/components/account/UserAccountMenu.vue";

const route = useRoute();
const isLoginRoute = computed(() => route.name === "login" || route.path.replace(/\/+$/, "") === "/login");

const mobileMedia = window.matchMedia?.("(max-width: 720px)") ?? null;
const isMobile = ref(mobileMedia?.matches ?? false);
const userCollapsed = ref(window.localStorage.getItem("visiox.sidebar.collapsed") === "true");
const mobileExpanded = ref(false);
const effectiveCollapsed = computed(() => isMobile.value ? !mobileExpanded.value : userCollapsed.value);
const sidebarWidth = computed(() => (effectiveCollapsed.value ? "64px" : "184px"));

function handleMobileChange(event: MediaQueryListEvent) {
  isMobile.value = event.matches;
  mobileExpanded.value = false;
}

onMounted(() => mobileMedia?.addEventListener("change", handleMobileChange));
onBeforeUnmount(() => mobileMedia?.removeEventListener("change", handleMobileChange));

function toggleSidebar() {
  if (isMobile.value) {
    mobileExpanded.value = !mobileExpanded.value;
    return;
  }
  userCollapsed.value = !userCollapsed.value;
  window.localStorage.setItem("visiox.sidebar.collapsed", String(userCollapsed.value));
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
