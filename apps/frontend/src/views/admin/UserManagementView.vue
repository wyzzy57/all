<template>
  <section class="identity-page">
    <header class="identity-page-header">
      <div>
        <h1>用户管理</h1>
        <p>创建账户、调整角色与状态，并管理登录凭据。</p>
      </div>
      <el-button type="primary" data-testid="create-user-button" @click="openCreate">创建用户</el-button>
    </header>

    <div class="identity-toolbar">
      <el-input v-model="filters.search" data-testid="user-search" clearable placeholder="搜索用户名、显示名称或邮箱" @keyup.enter="reloadFirstPage" />
      <el-select v-model="filters.role" clearable placeholder="全部角色" @change="reloadFirstPage">
        <el-option label="管理员" value="admin" />
        <el-option label="普通用户" value="member" />
      </el-select>
      <el-select v-model="filters.status" clearable placeholder="全部状态" @change="reloadFirstPage">
        <el-option label="启用" value="active" />
        <el-option label="禁用" value="disabled" />
        <el-option label="已删除" value="deleted" />
      </el-select>
      <el-button data-testid="user-search-submit" @click="reloadFirstPage">查询</el-button>
    </div>

    <el-table v-loading="loading" :data="users" row-key="id" class="identity-table">
      <el-table-column prop="username" label="用户名" min-width="130" />
      <el-table-column prop="display_name" label="显示名称" min-width="140" />
      <el-table-column prop="email" label="邮箱" min-width="210" />
      <el-table-column label="角色" width="110">
        <template #default="{ row }">{{ row.role === "admin" ? "管理员" : "普通用户" }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : row.status === 'disabled' ? 'warning' : 'info'" effect="plain">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status !== 'deleted'"
            link
            type="primary"
            :data-testid="`edit-user-${row.id}`"
            @click="openEdit(row)"
          >编辑</el-button>
          <el-button
            v-if="row.status !== 'deleted' && !isSelf(row)"
            link
            :type="row.status === 'active' ? 'warning' : 'success'"
            :data-testid="`toggle-user-${row.id}`"
            @click="toggleStatus(row)"
          >
            {{ row.status === "active" ? "禁用" : "启用" }}
          </el-button>
          <el-button
            v-if="row.status !== 'deleted'"
            link
            :data-testid="`reset-user-${row.id}`"
            @click="resetPassword(row)"
          >重置密码</el-button>
          <el-button
            v-if="row.status !== 'deleted' && !isSelf(row)"
            link
            type="danger"
            :data-testid="`delete-user-${row.id}`"
            @click="removeUser(row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="identity-pagination">
      <span>共 {{ total }} 个用户</span>
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        @current-change="loadUsers"
      />
    </div>

    <el-dialog v-model="editorVisible" :title="editingId ? '编辑用户' : '创建用户'" width="520px">
      <el-form id="user-editor-form" data-testid="user-editor-form" label-position="top" @submit.prevent="submitUser">
        <el-form-item v-if="!editingId" label="用户名" required>
          <el-input v-model="editor.username" data-testid="user-username" maxlength="120" />
        </el-form-item>
        <el-form-item label="显示名称" required>
          <el-input v-model="editor.displayName" data-testid="user-display-name" maxlength="160" />
        </el-form-item>
        <el-form-item label="邮箱" required>
          <el-input v-model="editor.email" data-testid="user-email" maxlength="320" />
        </el-form-item>
        <el-form-item label="角色" required>
          <el-radio-group v-model="editor.role">
            <el-radio value="member" data-testid="user-role-member" :disabled="editingSelfAdmin">普通用户</el-radio>
            <el-radio value="admin">管理员</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="!editingId" label="临时密码">
          <el-input v-model="editor.temporaryPassword" type="password" show-password placeholder="留空则由系统生成" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" native-type="submit" form="user-editor-form" data-testid="submit-user" :loading="saving">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="temporaryPasswordVisible" title="临时密码" width="460px">
      <el-alert title="该密码仅显示一次，请安全交付给用户。" type="warning" :closable="false" />
      <div class="temporary-password">{{ temporaryPassword }}</div>
      <template #footer><el-button type="primary" @click="temporaryPasswordVisible = false">我已记录</el-button></template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";

import { api, type UserRecord } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

const pageSize = 20;
const users = ref<UserRecord[]>([]);
const total = ref(0);
const page = ref(1);
const loading = ref(false);
const saving = ref(false);
const editorVisible = ref(false);
const temporaryPasswordVisible = ref(false);
const temporaryPassword = ref("");
const editingId = ref<string | null>(null);
const filters = reactive({ search: "", role: "", status: "" });
const editor = reactive({
  username: "",
  displayName: "",
  email: "",
  role: "member" as "admin" | "member",
  temporaryPassword: "",
});
const auth = useAuthStore();
const editingSelfAdmin = computed(() => editingId.value === auth.user?.id && auth.user?.role === "admin");
let listRequestId = 0;

onMounted(loadUsers);

async function loadUsers() {
  const requestId = ++listRequestId;
  loading.value = true;
  try {
    const response = await api.listUsers({
      search: filters.search.trim() || undefined,
      role: filters.role || undefined,
      status: filters.status || undefined,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    });
    if (requestId === listRequestId) {
      users.value = response.items;
      total.value = response.total;
    }
  } catch (error) {
    if (requestId === listRequestId) ElMessage.error(message(error, "用户列表加载失败"));
  } finally {
    if (requestId === listRequestId) loading.value = false;
  }
}

function reloadFirstPage() {
  page.value = 1;
  void loadUsers();
}

function openCreate() {
  editingId.value = null;
  Object.assign(editor, { username: "", displayName: "", email: "", role: "member", temporaryPassword: "" });
  editorVisible.value = true;
}

function openEdit(user: UserRecord) {
  editingId.value = user.id;
  Object.assign(editor, {
    username: user.username,
    displayName: user.display_name,
    email: user.email,
    role: user.role,
    temporaryPassword: "",
  });
  editorVisible.value = true;
}

async function submitUser() {
  if (!editor.displayName.trim() || !editor.email.trim() || (!editingId.value && !editor.username.trim())) {
    ElMessage.warning("请完整填写必填项");
    return;
  }
  saving.value = true;
  try {
    if (editingId.value) {
      await api.updateUser(editingId.value, {
        display_name: editor.displayName.trim(),
        email: editor.email.trim(),
        role: editingSelfAdmin.value ? "admin" : editor.role,
      });
      ElMessage.success("用户信息已更新");
    } else {
      const created = await api.createUser({
        username: editor.username.trim(),
        display_name: editor.displayName.trim(),
        email: editor.email.trim(),
        role: editor.role,
        ...(editor.temporaryPassword ? { temporary_password: editor.temporaryPassword } : {}),
      });
      if (created.temporary_password) showTemporaryPassword(created.temporary_password);
      ElMessage.success("用户已创建");
    }
    editorVisible.value = false;
    await loadUsers();
  } catch (error) {
    ElMessage.error(message(error, "用户保存失败"));
  } finally {
    saving.value = false;
  }
}

async function toggleStatus(user: UserRecord) {
  if (isSelf(user)) return;
  if (user.status === "active") {
    const accepted = await confirmed(`禁用后 ${user.username} 将无法登录，是否继续？`, "禁用用户");
    if (!accepted) return;
  }
  try {
    await api.updateUser(user.id, { status: user.status === "active" ? "disabled" : "active" });
    await loadUsers();
  } catch (error) {
    ElMessage.error(message(error, "用户状态更新失败"));
  }
}

async function resetPassword(user: UserRecord) {
  const accepted = await confirmed(`确定重置 ${user.username} 的密码吗？`, "重置密码");
  if (!accepted) return;
  try {
    const response = await api.resetUserPassword(user.id);
    showTemporaryPassword(response.temporary_password);
  } catch (error) {
    ElMessage.error(message(error, "密码重置失败"));
  }
}

async function removeUser(user: UserRecord) {
  if (isSelf(user)) return;
  const accepted = await confirmed(`删除后 ${user.username} 将无法登录，是否继续？`, "删除用户");
  if (!accepted) return;
  try {
    await api.deleteUser(user.id);
    ElMessage.success("用户已删除");
    await loadUsers();
  } catch (error) {
    ElMessage.error(message(error, "用户删除失败"));
  }
}

function showTemporaryPassword(value: string) {
  temporaryPassword.value = value;
  temporaryPasswordVisible.value = true;
}

function statusLabel(status: string) {
  return status === "active" ? "启用" : status === "disabled" ? "禁用" : "已删除";
}

function isSelf(user: UserRecord) {
  return user.id === auth.user?.id;
}

async function confirmed(content: string, title: string) {
  try {
    await ElMessageBox.confirm(content, title, { type: "warning" });
    return true;
  } catch {
    return false;
  }
}

function message(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
</script>
