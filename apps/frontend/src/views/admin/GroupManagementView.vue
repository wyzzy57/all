<template>
  <section class="identity-page">
    <header class="identity-page-header">
      <div><h1>用户分组</h1><p>按团队组织用户，并通过分组统一分配资源权限。</p></div>
      <el-button type="primary" data-testid="create-group-button" @click="openCreate">创建分组</el-button>
    </header>

    <el-table v-loading="loading" :data="groups" row-key="id" class="identity-table">
      <el-table-column prop="name" label="分组名称" min-width="180" />
      <el-table-column prop="description" label="说明" min-width="260" show-overflow-tooltip />
      <el-table-column prop="member_count" label="成员数" width="110" />
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :data-testid="`edit-group-${row.id}`" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" :data-testid="`delete-group-${row.id}`" @click="removeGroup(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="identity-pagination">
      <span>共 {{ total }} 个分组</span>
      <el-pagination data-testid="group-pagination" v-model:current-page="page" :page-size="pageSize" :total="total" layout="prev, pager, next" @current-change="loadGroups" />
    </div>

    <el-dialog v-model="editorVisible" :title="editingId ? '编辑分组' : '创建分组'" width="600px">
      <el-form id="group-editor-form" data-testid="group-editor-form" label-position="top" @submit.prevent="submitGroup">
        <el-form-item label="分组名称" required><el-input v-model="editor.name" data-testid="group-name" maxlength="160" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="editor.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="成员">
          <el-checkbox-group v-model="editor.memberIds" class="member-selector">
            <el-checkbox
              v-for="user in users"
              :key="user.id"
              :value="user.id"
              :data-testid="`group-member-${user.id}`"
            >
              {{ user.display_name }}（{{ user.username }}）
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" native-type="submit" form="group-editor-form" data-testid="submit-group" :loading="saving">确定</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { onMounted, reactive, ref } from "vue";

import { api, type UserGroupRecord, type UserRecord } from "@/api/client";

const pageSize = 20;
const groups = ref<UserGroupRecord[]>([]);
const users = ref<UserRecord[]>([]);
const total = ref(0);
const page = ref(1);
const loading = ref(false);
const saving = ref(false);
const editorVisible = ref(false);
const editingId = ref<string | null>(null);
const editor = reactive({ name: "", description: "", memberIds: [] as string[] });
let groupListRequestId = 0;

onMounted(async () => {
  await Promise.all([loadGroups(), loadUsers()]);
});

async function loadGroups() {
  const requestId = ++groupListRequestId;
  loading.value = true;
  try {
    const response = await api.listUserGroups({ limit: pageSize, offset: (page.value - 1) * pageSize });
    if (requestId === groupListRequestId) {
      groups.value = response.items;
      total.value = response.total;
    }
  } catch (error) {
    if (requestId === groupListRequestId) ElMessage.error(message(error, "分组列表加载失败"));
  } finally {
    if (requestId === groupListRequestId) loading.value = false;
  }
}

async function loadUsers() {
  try {
    users.value = await loadAllActiveUsers();
  } catch (error) {
    ElMessage.error(message(error, "可选成员加载失败"));
  }
}

function openCreate() {
  editingId.value = null;
  Object.assign(editor, { name: "", description: "", memberIds: [] });
  editorVisible.value = true;
}

function openEdit(group: UserGroupRecord) {
  editingId.value = group.id;
  Object.assign(editor, { name: group.name, description: group.description ?? "", memberIds: [...group.member_ids] });
  editorVisible.value = true;
}

async function submitGroup() {
  if (!editor.name.trim()) {
    ElMessage.warning("请输入分组名称");
    return;
  }
  saving.value = true;
  const wasCreate = !editingId.value;
  let group: UserGroupRecord;
  try {
    if (editingId.value) {
      group = await api.updateUserGroup(editingId.value, { name: editor.name.trim(), description: editor.description.trim() || null });
    } else {
      group = await api.createUserGroup({ name: editor.name.trim(), description: editor.description.trim() || null });
    }
    editingId.value = group.id;
  } catch (error) {
    ElMessage.error(message(error, "分组信息保存失败"));
    saving.value = false;
    return;
  }
  try {
    await api.replaceUserGroupMembers(group.id, [...editor.memberIds]);
    editorVisible.value = false;
    ElMessage.success("分组已保存");
    await loadGroups();
  } catch (error) {
    const prefix = wasCreate ? "分组已创建，但成员同步失败" : "分组信息已保存，但成员同步失败";
    ElMessage.warning(`${prefix}：${message(error, "请重试成员同步")}`);
    await loadGroups();
  } finally {
    saving.value = false;
  }
}

async function removeGroup(group: UserGroupRecord) {
  const accepted = await confirmed(`确定删除分组“${group.name}”吗？`, "删除分组");
  if (!accepted) return;
  try {
    await api.deleteUserGroup(group.id);
    await loadGroups();
    ElMessage.success("分组已删除");
  } catch (error) {
    ElMessage.error(message(error, "分组删除失败"));
  }
}

async function loadAllActiveUsers() {
  const pageSize = 200;
  const maxPages = 50;
  const items: UserRecord[] = [];
  let total = Number.POSITIVE_INFINITY;
  for (let pageIndex = 0; pageIndex < maxPages && items.length < total; pageIndex += 1) {
    const response = await api.listUsers({ status: "active", limit: pageSize, offset: pageIndex * pageSize });
    items.push(...response.items);
    total = response.total;
    if (!response.items.length) break;
  }
  if (items.length < total) ElMessage.warning(`成员数量超过 ${items.length} 条，请联系管理员缩小可选范围`);
  return items;
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
