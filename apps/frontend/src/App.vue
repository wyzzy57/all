<template>
  <div v-if="isLoginRoute" class="auth-route">
    <router-view />
  </div>
  <el-container v-else class="app-shell">
    <el-aside
      :width="sidebarWidth"
      class="app-sidebar"
      :class="{ collapsed: userCollapsed }"
    >
      <div class="sidebar-brand">
        <a class="brand" href="/workbench" aria-label="VisioX 首页" @click.prevent="$router.push('/workbench')">
          <span class="brand-word">Visio</span><span class="brand-x">X</span>
        </a>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <div v-for="(group, groupIndex) in navGroups" :key="groupIndex" class="nav-group">
          <el-menu
            :default-active="$route.path"
            :collapse="userCollapsed"
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
        <UserAccountMenu
          :collapsed="userCollapsed"
          @open-management="openManagement"
        />
      </div>

      <button
        class="sidebar-toggle"
        type="button"
        :aria-label="userCollapsed ? '展开侧边栏' : '收起侧边栏'"
        :title="userCollapsed ? '展开侧边栏' : '收起侧边栏'"
        @click="toggleSidebar"
      >
        <el-icon><ArrowRight v-if="userCollapsed" /><ArrowLeft v-else /></el-icon>
      </button>
    </el-aside>

    <el-container class="app-content-shell">
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>

    <ManagementCenterDialog
      v-if="managementOpen"
      v-model="managementOpen"
      :initial-section="managementSection"
    />
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
import { computed, defineAsyncComponent, ref } from "vue";
import { useRoute } from "vue-router";

import type { ManagementSection } from "@/components/account/ManagementCenterDialog.vue";
import UserAccountMenu from "@/components/account/UserAccountMenu.vue";

const ManagementCenterDialog = defineAsyncComponent(
  () => import("@/components/account/ManagementCenterDialog.vue"),
);

const route = useRoute();
const isLoginRoute = computed(() => route.name === "login" || route.path.replace(/\/+$/, "") === "/login");

const userCollapsed = ref(window.localStorage.getItem("visiox.sidebar.collapsed") === "true");
const sidebarWidth = computed(() => (userCollapsed.value ? "64px" : "224px"));
const managementOpen = ref(false);
const managementSection = ref<ManagementSection>("account");

function toggleSidebar() {
  userCollapsed.value = !userCollapsed.value;
  window.localStorage.setItem("visiox.sidebar.collapsed", String(userCollapsed.value));
}

function openManagement(section: ManagementSection) {
  managementSection.value = section;
  managementOpen.value = true;
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
