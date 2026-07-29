<template>
  <section class="identity-page account-page">
    <header class="identity-page-header">
      <div>
        <h1>账户设置</h1>
        <p>维护当前账户资料与登录密码。</p>
      </div>
    </header>

    <el-alert
      v-if="auth.user?.must_change_password"
      title="当前密码为临时密码，请完成修改后继续使用。"
      type="warning"
      show-icon
      :closable="false"
      class="password-warning"
    />

    <div class="account-sections">
      <section class="identity-section">
        <h2>个人资料</h2>
        <el-form data-testid="profile-form" label-position="top" class="identity-form" @submit.prevent="saveProfile">
          <el-form-item label="用户名">
            <el-input :model-value="auth.user?.username" disabled />
          </el-form-item>
          <el-form-item label="显示名称" required>
            <el-input v-model="profile.display_name" data-testid="profile-display-name" maxlength="160" show-word-limit />
          </el-form-item>
          <el-form-item label="邮箱" required>
            <el-input v-model="profile.email" data-testid="profile-email" maxlength="320" />
          </el-form-item>
          <el-button type="primary" data-testid="save-profile" :loading="profileSaving" native-type="submit">保存资料</el-button>
        </el-form>
      </section>

      <section class="identity-section">
        <h2>修改密码</h2>
        <el-form data-testid="password-form" label-position="top" class="identity-form" @submit.prevent="savePassword">
          <el-form-item label="当前密码" required>
            <el-input v-model="password.current" data-testid="current-password" type="password" show-password autocomplete="current-password" />
          </el-form-item>
          <el-form-item label="新密码" required>
            <el-input v-model="password.next" data-testid="new-password" type="password" show-password autocomplete="new-password" />
          </el-form-item>
          <el-form-item label="确认新密码" required>
            <el-input v-model="password.confirm" data-testid="confirm-password" type="password" show-password autocomplete="new-password" />
          </el-form-item>
          <p class="form-hint">至少 12 个字符。修改成功后需要重新登录。</p>
          <el-button type="primary" data-testid="save-password" :loading="passwordSaving" native-type="submit">修改密码</el-button>
        </el-form>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import { reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();
const profileSaving = ref(false);
const passwordSaving = ref(false);
const profile = reactive({ display_name: "", email: "" });
const password = reactive({ current: "", next: "", confirm: "" });

watch(
  () => auth.user,
  (user) => {
    profile.display_name = user?.display_name ?? "";
    profile.email = user?.email ?? "";
  },
  { immediate: true },
);

async function saveProfile() {
  if (!profile.display_name.trim() || !profile.email.trim()) {
    ElMessage.warning("请完整填写显示名称和邮箱");
    return;
  }
  profileSaving.value = true;
  try {
    await auth.updateProfile({ display_name: profile.display_name.trim(), email: profile.email.trim() });
    ElMessage.success("账户资料已更新");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "账户资料更新失败");
  } finally {
    profileSaving.value = false;
  }
}

async function savePassword() {
  if (password.next.length < 12) {
    ElMessage.warning("新密码至少需要 12 个字符");
    return;
  }
  if (password.next !== password.confirm) {
    ElMessage.warning("两次输入的新密码不一致");
    return;
  }
  passwordSaving.value = true;
  try {
    await auth.changePassword(password.current, password.next);
    ElMessage.success("密码已修改，请重新登录");
    await router.replace("/login");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "密码修改失败");
  } finally {
    passwordSaving.value = false;
  }
}
</script>
