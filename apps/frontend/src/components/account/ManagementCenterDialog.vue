<template>
  <el-dialog
    :model-value="modelValue"
    class="management-center-dialog"
    width="min(1320px, 94vw)"
    :show-close="false"
    :close-on-click-modal="true"
    append-to-body
    aria-label="账户与平台管理"
    @update:model-value="emit('update:modelValue', $event)"
    @closed="restoreAccountSection"
  >
    <div class="management-center-frame">
      <aside class="management-center-rail" aria-label="管理功能导航">
        <div class="management-center-rail-header">
          <button
            type="button"
            class="management-center-close"
            aria-label="关闭管理中心"
            title="关闭"
            @click="emit('update:modelValue', false)"
          >
            <el-icon><Close /></el-icon>
          </button>
          <strong>管理中心</strong>
        </div>

        <nav class="management-center-nav">
          <button
            v-for="section in availableSections"
            :key="section.key"
            type="button"
            class="management-center-nav-item"
            :class="{ 'is-active': activeSection === section.key }"
            :data-testid="`management-nav-${section.key}`"
            :aria-current="activeSection === section.key ? 'page' : undefined"
            @click="activeSection = section.key"
          >
            <el-icon><component :is="section.icon" /></el-icon>
            <span>{{ section.label }}</span>
          </button>
        </nav>
      </aside>

      <section class="management-center-content">
        <header class="management-center-content-header">
          <div>
            <h2>{{ activeMeta.label }}</h2>
            <p>{{ activeMeta.description }}</p>
          </div>
        </header>
        <div class="management-center-content-body">
          <component :is="activeMeta.view" />
        </div>
      </section>
    </div>
  </el-dialog>
</template>

<script lang="ts">
export type ManagementSection =
  | "account"
  | "overview"
  | "users"
  | "groups"
  | "authorization"
  | "audit"
  | "resources";
</script>

<script setup lang="ts">
import {
  Avatar,
  Close,
  DataAnalysis,
  DocumentChecked,
  Key,
  Monitor,
  User,
  UserFilled,
} from "@element-plus/icons-vue";
import type { Component } from "vue";
import { computed, ref, watch } from "vue";

import { useAuthStore } from "@/stores/auth";
import AccountView from "@/views/account/AccountView.vue";
import AdminOverviewView from "@/views/admin/AdminOverviewView.vue";
import AuditLogView from "@/views/admin/AuditLogView.vue";
import AuthorizationView from "@/views/admin/AuthorizationView.vue";
import GroupManagementView from "@/views/admin/GroupManagementView.vue";
import UserManagementView from "@/views/admin/UserManagementView.vue";
import NodeManagementView from "@/views/resources/NodeManagementView.vue";

interface ManagementEntry {
  key: ManagementSection;
  label: string;
  description: string;
  icon: Component;
  view: Component;
  adminOnly?: boolean;
}

const props = withDefaults(defineProps<{
  modelValue: boolean;
  initialSection?: ManagementSection;
}>(), {
  initialSection: "account",
});

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();

const auth = useAuthStore();
const sections: ManagementEntry[] = [
  {
    key: "account",
    label: "账户设置",
    description: "维护个人资料、邮箱和登录密码。",
    icon: User,
    view: AccountView,
  },
  {
    key: "overview",
    label: "平台总览",
    description: "查看平台资源、用户和业务运行概况。",
    icon: DataAnalysis,
    view: AdminOverviewView,
    adminOnly: true,
  },
  {
    key: "users",
    label: "用户管理",
    description: "创建账户并管理角色、状态和密码。",
    icon: UserFilled,
    view: UserManagementView,
    adminOnly: true,
  },
  {
    key: "groups",
    label: "用户分组",
    description: "组织用户并统一维护分组成员。",
    icon: Avatar,
    view: GroupManagementView,
    adminOnly: true,
  },
  {
    key: "authorization",
    label: "资源授权与分配",
    description: "配置资源所有权、公开范围与用户授权。",
    icon: Key,
    view: AuthorizationView,
    adminOnly: true,
  },
  {
    key: "audit",
    label: "审计日志",
    description: "追踪关键操作、访问结果和安全事件。",
    icon: DocumentChecked,
    view: AuditLogView,
    adminOnly: true,
  },
  {
    key: "resources",
    label: "节点与资源",
    description: "接入服务器并查看 CPU、内存、磁盘和 GPU 资源。",
    icon: Monitor,
    view: NodeManagementView,
    adminOnly: true,
  },
];

const availableSections = computed(() => sections.filter((section) => !section.adminOnly || auth.isAdmin));
const activeSection = ref<ManagementSection>("account");
const activeMeta = computed(() => (
  availableSections.value.find((section) => section.key === activeSection.value)
  ?? availableSections.value[0]
  ?? sections[0]
));

function normalizeSection(section: ManagementSection): ManagementSection {
  return availableSections.value.some((entry) => entry.key === section) ? section : "account";
}

function restoreAccountSection() {
  activeSection.value = "account";
}

watch(
  () => [props.modelValue, props.initialSection, auth.isAdmin] as const,
  ([open, section]) => {
    if (open) activeSection.value = normalizeSection(section);
  },
  { immediate: true },
);
</script>

<style scoped>
:global(.management-center-dialog) {
  height: min(760px, calc(100vh - 40px));
  margin-top: max(20px, 5vh);
  padding: 0;
  overflow: hidden;
  border: 1px solid #d9d9d9;
  border-radius: 16px;
  box-shadow: 0 18px 60px rgb(0 0 0 / 16%);
}

:global(.management-center-dialog .el-dialog__header) {
  display: none;
}

:global(.management-center-dialog .el-dialog__body) {
  height: 100%;
  max-height: none;
  padding: 0;
}

.management-center-frame {
  display: grid;
  height: 100%;
  min-width: 0;
  grid-template-columns: 220px minmax(0, 1fr);
  background: #ffffff;
}

.management-center-rail {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
  padding: 12px 10px;
  border-right: 1px solid #e5e5e5;
  background: #f7f7f7;
}

.management-center-rail-header {
  display: flex;
  min-height: 40px;
  align-items: center;
  gap: 10px;
  padding: 0 4px;
  color: #171717;
}

.management-center-close {
  display: grid;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #303030;
  cursor: pointer;
}

.management-center-close:hover,
.management-center-close:focus-visible {
  background: #e7e7e7;
}

.management-center-close:focus-visible,
.management-center-nav-item:focus-visible {
  outline: 2px solid #4d8dff;
  outline-offset: 1px;
}

.management-center-nav {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  overflow: hidden auto;
}

.management-center-nav-item {
  display: flex;
  width: 100%;
  min-height: 40px;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #303030;
  cursor: pointer;
  font-size: 14px;
  text-align: left;
}

.management-center-nav-item:hover,
.management-center-nav-item.is-active {
  background: #e7e7e7;
  color: #171717;
}

.management-center-nav-item.is-active {
  font-weight: 600;
}

.management-center-content {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  background: #ffffff;
}

.management-center-content-header {
  flex: 0 0 auto;
  padding: 22px 28px 18px;
  border-bottom: 1px solid #e8e8e8;
}

.management-center-content-header h2,
.management-center-content-header p {
  margin: 0;
}

.management-center-content-header h2 {
  color: #171717;
  font-size: 18px;
  line-height: 1.5;
}

.management-center-content-header p {
  margin-top: 4px;
  color: #6b6b6b;
  font-size: 13px;
}

.management-center-content-body {
  min-width: 0;
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 24px 28px 32px;
  scrollbar-gutter: stable;
}

.management-center-loading {
  display: grid;
  min-height: 180px;
  place-items: center;
  color: #6b6b6b;
  font-size: 14px;
}

.management-center-content-body :deep(.identity-page) {
  max-width: none;
}

.management-center-content-body :deep(.identity-page-header) {
  display: none;
}

@media (max-width: 720px) {
  :global(.management-center-dialog) {
    width: calc(100vw - 16px) !important;
    height: calc(100dvh - 16px);
    margin: 8px auto;
    border-radius: 14px;
  }

  .management-center-frame {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto minmax(0, 1fr);
  }

  .management-center-rail {
    gap: 4px;
    padding: 8px;
    border-right: 0;
    border-bottom: 1px solid #e5e5e5;
  }

  .management-center-nav {
    flex-direction: row;
    overflow: auto hidden;
  }

  .management-center-nav-item {
    width: auto;
    min-width: max-content;
  }

  .management-center-content-header,
  .management-center-content-body {
    padding-right: 16px;
    padding-left: 16px;
  }
}
</style>
