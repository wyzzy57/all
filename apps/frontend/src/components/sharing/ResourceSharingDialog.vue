<template>
  <el-dialog
    :model-value="modelValue"
    width="680px"
    class="resource-sharing-dialog"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="dialog-heading">
        <strong>访问与共享</strong>
        <span>{{ resourceName }}</span>
      </div>
    </template>

    <div v-loading="loading" class="sharing-content">
      <p class="section-label">谁可以访问</p>
      <div class="scope-options" role="radiogroup" aria-label="共享范围">
        <button
          type="button"
          class="scope-option"
          :class="{ active: mode === 'private' }"
          data-testid="sharing-private"
          @click="mode = 'private'"
        >
          <el-icon><Lock /></el-icon>
          <span><strong>仅自己</strong><small>只有所有者和管理员可以访问</small></span>
          <i aria-hidden="true" />
        </button>
        <button
          v-if="allowOrganization"
          type="button"
          class="scope-option"
          :class="{ active: mode === 'organization' }"
          data-testid="sharing-organization"
          @click="mode = 'organization'"
        >
          <el-icon><OfficeBuilding /></el-icon>
          <span><strong>全平台成员</strong><small>组织内登录用户按所选权限访问</small></span>
          <i aria-hidden="true" />
        </button>
        <button
          type="button"
          class="scope-option"
          :class="{ active: mode === 'specific' }"
          data-testid="sharing-specific"
          @click="mode = 'specific'"
        >
          <el-icon><User /></el-icon>
          <span><strong>指定成员或分组</strong><small>精确选择可访问此资源的对象</small></span>
          <i aria-hidden="true" />
        </button>
      </div>

      <section v-if="mode !== 'private'" class="permission-section">
        <div class="section-heading">
          <p class="section-label">允许的操作</p>
          <span>至少选择一项</span>
        </div>
        <el-checkbox-group v-model="permissions" class="permission-options">
          <el-checkbox v-for="item in permissionOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </el-checkbox>
        </el-checkbox-group>
      </section>

      <section v-if="mode === 'specific'" class="principal-section">
        <p class="section-label">成员与分组</p>
        <div class="principal-list">
          <label v-for="user in principals.users" :key="`user-${user.id}`" class="principal-row">
            <el-checkbox
              :model-value="selectedPrincipals.has(`user:${user.id}`)"
              @change="togglePrincipal('user', user.id, Boolean($event))"
            />
            <span class="principal-avatar">{{ user.display_name.slice(0, 1) }}</span>
            <span><strong>{{ user.display_name }}</strong><small>@{{ user.username }}</small></span>
          </label>
          <label v-for="group in principals.groups" :key="`group-${group.id}`" class="principal-row">
            <el-checkbox
              :model-value="selectedPrincipals.has(`group:${group.id}`)"
              @change="togglePrincipal('group', group.id, Boolean($event))"
            />
            <span class="principal-avatar group"><el-icon><UserFilled /></el-icon></span>
            <span><strong>{{ group.name }}</strong><small>用户分组</small></span>
          </label>
          <el-empty v-if="principals.users.length === 0 && principals.groups.length === 0" :image-size="56" description="暂无可共享的成员或分组" />
        </div>
      </section>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="!canSave"
        data-testid="save-sharing"
        @click="saveSharing"
      >
        保存配置
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { Lock, OfficeBuilding, User, UserFilled } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, reactive, ref, watch } from "vue";

import {
  api,
  type ResourceSharingGrantInput,
  type SharingPrincipalList,
} from "@/api/client";

const props = defineProps<{
  modelValue: boolean;
  resourceType: string;
  resourceId: string;
  resourceName: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  saved: [visibility: string];
}>();

type SharingMode = "private" | "organization" | "specific";

const loading = ref(false);
const saving = ref(false);
const error = ref("");
const mode = ref<SharingMode>("private");
const permissions = ref<string[]>(["view"]);
const selectedPrincipals = ref(new Set<string>());
const principals = reactive<SharingPrincipalList>({
  organization: { id: "", name: "" },
  users: [],
  groups: [],
});

const allowOrganization = computed(() => !["node", "resource_pool"].includes(props.resourceType));
const permissionOptions = computed(() => {
  const values = [
    { value: "view", label: "查看" },
    { value: "use", label: "使用" },
  ];
  if (props.resourceType === "service") values.push({ value: "invoke", label: "调用" });
  return values;
});
const canSave = computed(() => {
  if (mode.value === "private") return true;
  if (permissions.value.length === 0) return false;
  return mode.value !== "specific" || selectedPrincipals.value.size > 0;
});

async function loadSharing() {
  if (!props.resourceId) return;
  loading.value = true;
  error.value = "";
  try {
    const [sharing, available] = await Promise.all([
      api.getResourceSharing(props.resourceType, props.resourceId),
      api.listSharingPrincipals(),
    ]);
    Object.assign(principals, available);
    const organizationGrant = sharing.grants.find((grant) => grant.principal_type === "organization");
    const directedGrants = sharing.grants.filter((grant) => grant.principal_type !== "organization");
    mode.value = organizationGrant ? "organization" : directedGrants.length ? "specific" : "private";
    permissions.value = [...(organizationGrant?.permissions ?? directedGrants[0]?.permissions ?? ["view"])];
    selectedPrincipals.value = new Set(
      directedGrants.map((grant) => `${grant.principal_type}:${grant.principal_id}`),
    );
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "共享配置加载失败";
  } finally {
    loading.value = false;
  }
}

function togglePrincipal(type: "user" | "group", id: string, selected: boolean) {
  const next = new Set(selectedPrincipals.value);
  const key = `${type}:${id}`;
  if (selected) next.add(key);
  else next.delete(key);
  selectedPrincipals.value = next;
}

async function saveSharing() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = "";
  try {
    let grants: ResourceSharingGrantInput[] = [];
    if (mode.value === "organization") {
      grants = [{
        principal_type: "organization",
        principal_id: principals.organization.id,
        permissions: permissions.value,
      }];
    } else if (mode.value === "specific") {
      grants = Array.from(selectedPrincipals.value).map((key) => {
        const [principal_type, principal_id] = key.split(":") as ["user" | "group", string];
        return { principal_type, principal_id, permissions: permissions.value };
      });
    }
    const result = await api.replaceResourceSharing(props.resourceType, props.resourceId, { grants });
    emit("saved", result.visibility);
    emit("update:modelValue", false);
    ElMessage.success("共享配置已更新");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "共享配置保存失败";
  } finally {
    saving.value = false;
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) void loadSharing();
  },
  { immediate: true },
);
</script>

<style scoped>
.dialog-heading { display: flex; flex-direction: column; gap: 4px; }
.dialog-heading strong { color: #172033; font-size: 18px; }
.dialog-heading span { color: #718096; font-size: 13px; }
.sharing-content { min-height: 260px; }
.section-label { margin: 0 0 10px; color: #27364d; font-size: 14px; font-weight: 600; }
.scope-options { display: grid; gap: 8px; }
.scope-option { position: relative; display: grid; grid-template-columns: 36px 1fr 16px; gap: 12px; align-items: center; min-height: 64px; padding: 10px 14px; border: 1px solid #dce3ed; border-radius: 6px; background: #fff; color: #56657a; text-align: left; cursor: pointer; transition: border-color 160ms ease, background-color 160ms ease; }
.scope-option:hover { border-color: #9dbcf7; background: #f8fbff; }
.scope-option.active { border-color: #2f78f6; background: #f4f8ff; }
.scope-option > .el-icon { width: 36px; height: 36px; border-radius: 6px; background: #edf4ff; color: #2f78f6; font-size: 18px; }
.scope-option span { display: flex; flex-direction: column; gap: 4px; }
.scope-option strong { color: #182235; font-size: 14px; }
.scope-option small { color: #718096; font-size: 12px; }
.scope-option i { width: 14px; height: 14px; border: 1px solid #c9d3e0; border-radius: 50%; }
.scope-option.active i { border: 4px solid #2f78f6; }
.permission-section, .principal-section { margin-top: 20px; padding-top: 18px; border-top: 1px solid #edf0f5; }
.section-heading { display: flex; justify-content: space-between; align-items: center; }
.section-heading span { color: #8b97aa; font-size: 12px; }
.permission-options { display: flex; gap: 20px; }
.principal-list { max-height: 230px; overflow-y: auto; border: 1px solid #e2e7ef; border-radius: 6px; }
.principal-row { display: grid; grid-template-columns: 22px 34px 1fr; gap: 10px; align-items: center; min-height: 54px; padding: 4px 12px; border-bottom: 1px solid #edf0f5; cursor: pointer; }
.principal-row:last-child { border-bottom: 0; }
.principal-row:hover { background: #f8fafc; }
.principal-avatar { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 6px; background: #eaf2ff; color: #246de0; font-weight: 600; }
.principal-avatar.group { background: #eef8f2; color: #1c9460; }
.principal-row > span:last-child { display: flex; flex-direction: column; gap: 2px; }
.principal-row strong { color: #27364d; font-size: 13px; }
.principal-row small { color: #8793a6; font-size: 12px; }
@media (max-width: 720px) {
  .scope-option { grid-template-columns: 32px 1fr 16px; padding-inline: 10px; }
  .permission-options { flex-wrap: wrap; gap: 8px 16px; }
}
</style>
