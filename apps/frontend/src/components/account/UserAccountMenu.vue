<template>
  <el-dropdown
    class="account-menu"
    placement="top-start"
    trigger="click"
    :teleported="false"
    popper-class="account-menu-popper"
    @command="handleCommand"
    @visible-change="menuOpen = $event"
  >
    <button
      class="account-trigger"
      :class="{ 'is-open': menuOpen }"
      type="button"
      data-testid="account-trigger"
      :aria-label="`账户菜单：${auth.user?.display_name || auth.user?.username || '用户'}`"
    >
      <el-avatar data-testid="account-avatar" :size="32">{{ initials }}</el-avatar>
      <span v-if="!collapsed" class="account-copy">
        <strong data-testid="account-name">{{ auth.user?.display_name || auth.user?.username }}</strong>
        <small data-testid="account-role">{{ roleLabel }}</small>
      </span>
      <el-icon v-if="!collapsed" class="account-chevron"><ArrowUp /></el-icon>
    </button>

    <template #dropdown>
      <el-dropdown-menu class="account-dropdown">
        <div class="account-dropdown-profile">
          <el-avatar :size="34">{{ initials }}</el-avatar>
          <span>
            <strong>{{ auth.user?.display_name || auth.user?.username }}</strong>
            <small>{{ roleLabel }}</small>
          </span>
        </div>
        <el-dropdown-item command="account" data-testid="profile-entry">
          <el-icon><User /></el-icon>账户设置
        </el-dropdown-item>
        <template v-if="auth.isAdmin">
          <el-dropdown-item command="admin-overview" divided data-testid="admin-overview-entry">
            <el-icon><DataAnalysis /></el-icon>平台总览
          </el-dropdown-item>
          <el-dropdown-item command="admin-users" data-testid="admin-user-entry">
            <el-icon><UserFilled /></el-icon>用户管理
          </el-dropdown-item>
          <el-dropdown-item command="admin-groups" data-testid="admin-group-entry">
            <el-icon><Avatar /></el-icon>用户分组
          </el-dropdown-item>
          <el-dropdown-item command="admin-authorization" data-testid="admin-authorization-entry">
            <el-icon><Key /></el-icon>资源授权与分配
          </el-dropdown-item>
          <el-dropdown-item command="admin-audit" data-testid="admin-audit-entry">
            <el-icon><DocumentChecked /></el-icon>审计日志
          </el-dropdown-item>
          <el-dropdown-item command="admin-resources" data-testid="admin-resources-entry">
            <el-icon><Monitor /></el-icon>节点与资源
          </el-dropdown-item>
        </template>
        <el-dropdown-item
          command="logout"
          divided
          data-testid="logout-entry"
        >
          <el-icon><SwitchButton /></el-icon>退出登录
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup lang="ts">
import { ArrowUp, Avatar, DataAnalysis, DocumentChecked, Key, Monitor, SwitchButton, User, UserFilled } from "@element-plus/icons-vue";
import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import type { ManagementSection } from "@/components/account/ManagementCenterDialog.vue";
import { useAuthStore } from "@/stores/auth";

defineProps<{ collapsed: boolean }>();
const emit = defineEmits<{
  "open-management": [section: ManagementSection];
}>();

const auth = useAuthStore();
const router = useRouter();
const menuOpen = ref(false);
const initials = computed(() => {
  const value = auth.user?.display_name || auth.user?.username || "U";
  return value.trim().slice(0, 1).toUpperCase();
});
const roleLabel = computed(() => (auth.isAdmin ? "管理员" : "普通用户"));

async function handleCommand(command: string) {
  const sections: Record<string, ManagementSection> = {
    account: "account",
    "admin-overview": "overview",
    "admin-users": "users",
    "admin-groups": "groups",
    "admin-authorization": "authorization",
    "admin-audit": "audit",
    "admin-resources": "resources",
  };
  if (command === "logout") {
    try {
      await auth.logout();
    } catch {
      // The store clears the local session even if the server cannot be reached.
    }
    await router.replace("/login");
    return;
  }
  const section = sections[command];
  if (section) emit("open-management", section);
}
</script>
