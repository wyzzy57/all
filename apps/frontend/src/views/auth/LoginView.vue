<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { safeRedirectTarget } from "@/router";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const username = ref("");
const password = ref("");
const submitting = ref(false);
const submitError = ref<string | null>(null);

const isSubmitDisabled = computed(() => (
  submitting.value || auth.loading || !username.value.trim() || !password.value
));
const visibleError = computed(() => submitError.value || auth.error);

async function submit(): Promise<void> {
  if (isSubmitDisabled.value) return;
  submitting.value = true;
  submitError.value = null;
  try {
    await auth.login(username.value.trim(), password.value);
    await router.replace(safeRedirectTarget(route.query.redirect));
  } catch (caught) {
    submitError.value = auth.error
      || (caught instanceof Error ? caught.message : "登录失败，请稍后重试");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-brand" aria-label="VisiOX">
        <span>Visio</span><i>X</i>
      </div>
      <div class="login-heading">
        <h1 id="login-title">登录 VisiOX</h1>
        <p>使用平台账户继续访问模型训练与部署工作台</p>
      </div>

      <el-alert
        v-if="visibleError"
        class="login-error"
        :title="visibleError"
        type="error"
        :closable="false"
        show-icon
      />

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名" required>
          <el-input
            v-model="username"
            data-testid="login-username"
            autocomplete="username"
            placeholder="请输入用户名"
            size="large"
          />
        </el-form-item>
        <el-form-item label="密码" required>
          <el-input
            v-model="password"
            data-testid="login-password"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
            size="large"
            show-password
          />
        </el-form-item>
        <el-button
          class="login-submit"
          data-testid="login-submit"
          type="primary"
          native-type="submit"
          size="large"
          :loading="submitting || auth.loading"
          :disabled="isSubmitDisabled"
        >
          登录
        </el-button>
      </el-form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  display: grid;
  min-height: calc(100vh - 90px);
  padding: 48px 20px;
  place-items: center;
  background: #ffffff;
}

.login-panel {
  width: min(100%, 400px);
  padding: 32px;
  border: 1px solid var(--visiox-border, #dfe5ef);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 12px 32px rgb(15 23 42 / 8%);
}

.login-brand {
  display: inline-flex;
  align-items: baseline;
  color: #171b24;
  font-family: Arial, "Helvetica Neue", sans-serif;
  font-size: 28px;
  font-weight: 800;
  line-height: 1;
}

.login-brand i {
  margin-left: 1px;
  color: #3269eb;
  font-style: italic;
  transform: skewX(-9deg);
}

.login-heading {
  margin: 28px 0 24px;
}

.login-heading h1 {
  margin: 0 0 8px;
  color: #172033;
  font-size: 22px;
  line-height: 1.4;
}

.login-heading p {
  margin: 0;
  color: #667085;
  font-size: 14px;
  line-height: 1.6;
}

.login-error {
  margin-bottom: 18px;
}

.login-submit {
  width: 100%;
  margin-top: 6px;
}

@media (max-width: 520px) {
  .login-page {
    padding: 24px 16px;
  }

  .login-panel {
    padding: 24px 20px;
    border-right: 0;
    border-left: 0;
    box-shadow: none;
  }
}
</style>
