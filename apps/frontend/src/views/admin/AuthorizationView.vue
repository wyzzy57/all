<template>
  <section class="identity-page">
    <header class="identity-page-header">
      <div><h1>资源授权与分配</h1><p>管理用户及分组对业务资源的访问权限和计算资源配额。</p></div>
    </header>

    <el-tabs v-model="activeTab" class="authorization-tabs">
      <el-tab-pane label="资源授权" name="grants">
        <div class="identity-section-heading">
          <span>授权策略</span>
          <el-button type="primary" data-testid="create-grant-button" @click="openGrant">新增授权</el-button>
        </div>
        <el-table v-loading="grantsLoading" :data="grants" row-key="id" class="identity-table">
          <el-table-column prop="resource_type" label="资源类型" min-width="130" />
          <el-table-column prop="resource_id" label="资源 ID" min-width="190" show-overflow-tooltip />
          <el-table-column label="授权对象" min-width="180">
            <template #default="{ row }">{{ principalLabel(row.principal_type, row.principal_id) }}</template>
          </el-table-column>
          <el-table-column label="权限" min-width="220">
            <template #default="{ row }">
              <el-tag v-for="permission in row.permissions" :key="permission" effect="plain" class="permission-tag">{{ permission }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="expires_at" label="失效时间" min-width="170">
            <template #default="{ row }">{{ row.expires_at || "长期有效" }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="danger" :data-testid="`delete-grant-${row.id}`" @click="removeGrant(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="identity-pagination">
          <span>共 {{ grantsTotal }} 条授权</span>
          <el-pagination
            v-model:current-page="grantPage"
            data-testid="grant-pagination"
            :page-size="pageSize"
            :total="grantsTotal"
            layout="prev, pager, next"
            @current-change="loadGrants"
          />
        </div>
      </el-tab-pane>

      <el-tab-pane label="资源配额" name="allocations">
        <div class="identity-section-heading">
          <span>分配策略</span>
          <el-button type="primary" data-testid="create-allocation-button" @click="openAllocation">新增配额</el-button>
        </div>
        <el-table v-loading="allocationsLoading" :data="allocations" row-key="id" class="identity-table">
          <el-table-column label="分配对象" min-width="180">
            <template #default="{ row }">{{ principalLabel(row.principal_type, row.principal_id) }}</template>
          </el-table-column>
          <el-table-column label="资源池" min-width="180">
            <template #default="{ row }">{{ poolLabel(row.resource_pool_id) }}</template>
          </el-table-column>
          <el-table-column prop="max_concurrent_training_jobs" label="并发训练" width="110" />
          <el-table-column prop="max_gpu_count" label="GPU 数" width="100" />
          <el-table-column prop="max_service_instances" label="服务实例" width="110" />
          <el-table-column label="失效时间" min-width="170">
            <template #default="{ row }">{{ row.expires_at || "长期有效" }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="danger" :data-testid="`delete-allocation-${row.id}`" @click="removeAllocation(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="identity-pagination">
          <span>共 {{ allocationsTotal }} 条配额</span>
          <el-pagination
            v-model:current-page="allocationPage"
            data-testid="allocation-pagination"
            :page-size="pageSize"
            :total="allocationsTotal"
            layout="prev, pager, next"
            @current-change="loadAllocations"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="grantVisible" title="新增资源授权" width="600px">
      <el-form id="grant-editor-form" data-testid="grant-editor-form" label-position="top" @submit.prevent="submitGrant">
        <div class="identity-form-grid">
          <el-form-item label="资源类型" required><el-input v-model="grantForm.resourceType" data-testid="grant-resource-type" placeholder="例如 dataset" /></el-form-item>
          <el-form-item label="资源 ID" required><el-input v-model="grantForm.resourceId" data-testid="grant-resource-id" /></el-form-item>
          <el-form-item label="对象类型" required>
            <el-select v-model="grantForm.principalType" @change="selectFirstPrincipal">
              <el-option label="用户" value="user" /><el-option label="分组" value="group" />
            </el-select>
          </el-form-item>
          <el-form-item label="授权对象" required>
            <el-select v-model="grantForm.principalId" filterable>
              <el-option v-for="item in availablePrincipals" :key="item.id" :label="item.label" :value="item.id" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="权限" required>
          <el-checkbox-group v-model="grantForm.permissions">
            <el-checkbox v-for="permission in permissionOptions" :key="permission" :value="permission">{{ permission }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="grantVisible = false">取消</el-button>
        <el-button type="primary" native-type="submit" form="grant-editor-form" data-testid="submit-grant" :loading="saving">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="allocationVisible" title="新增资源配额" width="640px">
      <el-form id="allocation-editor-form" data-testid="allocation-editor-form" label-position="top" @submit.prevent="submitAllocation">
        <div class="identity-form-grid">
          <el-form-item label="对象类型" required>
            <el-select v-model="allocationForm.principalType" @change="selectFirstAllocationPrincipal">
              <el-option label="用户" value="user" /><el-option label="分组" value="group" />
            </el-select>
          </el-form-item>
          <el-form-item label="分配对象" required>
            <el-select v-model="allocationForm.principalId" filterable>
              <el-option v-for="item in allocationPrincipals" :key="item.id" :label="item.label" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="资源池" required>
            <el-select v-model="allocationForm.resourcePoolId">
              <el-option v-for="pool in pools" :key="pool.id" :label="pool.name" :value="pool.id" />
            </el-select>
          </el-form-item>
        </div>
        <div class="identity-form-grid identity-form-grid-three">
          <el-form-item label="最大并发训练任务"><el-input-number v-model="allocationForm.maxTrainingJobs" :min="0" /></el-form-item>
          <el-form-item label="最大 GPU 数"><el-input-number v-model="allocationForm.maxGpuCount" :min="0" /></el-form-item>
          <el-form-item label="最大服务实例"><el-input-number v-model="allocationForm.maxServiceInstances" :min="0" /></el-form-item>
        </div>
        <el-form-item label="失效时间">
          <el-date-picker
            v-model="allocationForm.expiresAt"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
            placeholder="留空表示长期有效"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="allocationVisible = false">取消</el-button>
        <el-button type="primary" native-type="submit" form="allocation-editor-form" data-testid="submit-allocation" :loading="saving">确定</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";

import {
  api,
  type ResourceAllocationRecord,
  type ResourceGrantRecord,
  type ResourcePoolRecord,
  type UserGroupRecord,
  type UserRecord,
} from "@/api/client";

type PrincipalType = "user" | "group";
const pageSize = 20;
const selectorPageSize = 200;
const maxSelectorPages = 50;
const permissionOptions = ["view", "use", "edit", "delete", "manage", "invoke"] as const;
const activeTab = ref("grants");
const saving = ref(false);
const grantsLoading = ref(false);
const allocationsLoading = ref(false);
const grantVisible = ref(false);
const allocationVisible = ref(false);
const grants = ref<ResourceGrantRecord[]>([]);
const allocations = ref<ResourceAllocationRecord[]>([]);
const users = ref<UserRecord[]>([]);
const groups = ref<UserGroupRecord[]>([]);
const pools = ref<ResourcePoolRecord[]>([]);
const grantsTotal = ref(0);
const allocationsTotal = ref(0);
const grantPage = ref(1);
const allocationPage = ref(1);
const grantForm = reactive({
  resourceType: "",
  resourceId: "",
  principalType: "user" as PrincipalType,
  principalId: "",
  permissions: ["view"] as string[],
});
const allocationForm = reactive({
  principalType: "user" as PrincipalType,
  principalId: "",
  resourcePoolId: "",
  maxTrainingJobs: 1,
  maxGpuCount: 1,
  maxServiceInstances: 1,
  expiresAt: "",
});
let grantListRequestId = 0;
let allocationListRequestId = 0;

const userPrincipals = computed(() => users.value.map((user) => ({ id: user.id, label: `${user.display_name}（${user.username}）` })));
const groupPrincipals = computed(() => groups.value.map((group) => ({ id: group.id, label: group.name })));
const availablePrincipals = computed(() => grantForm.principalType === "user" ? userPrincipals.value : groupPrincipals.value);
const allocationPrincipals = computed(() => allocationForm.principalType === "user" ? userPrincipals.value : groupPrincipals.value);

onMounted(() => {
  void Promise.allSettled([loadGrants(), loadAllocations(), loadAuxiliaryOptions()]);
});

async function loadGrants() {
  const requestId = ++grantListRequestId;
  grantsLoading.value = true;
  try {
    const response = await api.listResourceGrants({ limit: pageSize, offset: (grantPage.value - 1) * pageSize });
    if (requestId === grantListRequestId) {
      grants.value = response.items;
      grantsTotal.value = response.total;
    }
  } catch (error) {
    if (requestId === grantListRequestId) ElMessage.error(message(error, "资源授权加载失败"));
  } finally {
    if (requestId === grantListRequestId) grantsLoading.value = false;
  }
}

async function loadAllocations() {
  const requestId = ++allocationListRequestId;
  allocationsLoading.value = true;
  try {
    const response = await api.listResourceAllocations({ limit: pageSize, offset: (allocationPage.value - 1) * pageSize });
    if (requestId === allocationListRequestId) {
      allocations.value = response.items;
      allocationsTotal.value = response.total;
    }
  } catch (error) {
    if (requestId === allocationListRequestId) ElMessage.error(message(error, "资源配额加载失败"));
  } finally {
    if (requestId === allocationListRequestId) allocationsLoading.value = false;
  }
}

async function loadAuxiliaryOptions() {
  const results = await Promise.allSettled([loadAllActiveUsers(), loadAllGroups(), api.listResourcePools()]);
  const [userResult, groupResult, poolResult] = results;
  if (userResult.status === "fulfilled") users.value = userResult.value;
  else ElMessage.error(`用户选项加载失败：${message(userResult.reason, "未知错误")}`);
  if (groupResult.status === "fulfilled") groups.value = groupResult.value;
  else ElMessage.error(`分组选项加载失败：${message(groupResult.reason, "未知错误")}`);
  if (poolResult.status === "fulfilled") pools.value = poolResult.value.items;
  else ElMessage.error(`资源池选项加载失败：${message(poolResult.reason, "未知错误")}`);
}

async function loadAllActiveUsers() {
  const items: UserRecord[] = [];
  let total = Number.POSITIVE_INFINITY;
  for (let index = 0; index < maxSelectorPages && items.length < total; index += 1) {
    const response = await api.listUsers({ status: "active", limit: selectorPageSize, offset: index * selectorPageSize });
    items.push(...response.items);
    total = response.total;
    if (!response.items.length) break;
  }
  if (items.length < total) ElMessage.warning(`用户数量超过 ${items.length} 条，请缩小授权范围`);
  return items;
}

async function loadAllGroups() {
  const items: UserGroupRecord[] = [];
  let total = Number.POSITIVE_INFINITY;
  for (let index = 0; index < maxSelectorPages && items.length < total; index += 1) {
    const response = await api.listUserGroups({ limit: selectorPageSize, offset: index * selectorPageSize });
    items.push(...response.items);
    total = response.total;
    if (!response.items.length) break;
  }
  if (items.length < total) ElMessage.warning(`分组数量超过 ${items.length} 条，请缩小授权范围`);
  return items;
}

function openGrant() {
  Object.assign(grantForm, { resourceType: "", resourceId: "", principalType: "user", principalId: userPrincipals.value[0]?.id ?? "", permissions: ["view"] });
  grantVisible.value = true;
}

function selectFirstPrincipal() {
  grantForm.principalId = availablePrincipals.value[0]?.id ?? "";
}

async function submitGrant() {
  if (!grantForm.resourceType.trim() || !grantForm.resourceId.trim() || !grantForm.principalId || !grantForm.permissions.length) {
    ElMessage.warning("请完整填写授权信息");
    return;
  }
  saving.value = true;
  try {
    await api.upsertResourceGrant({
      resource_type: grantForm.resourceType.trim(), resource_id: grantForm.resourceId.trim(),
      principal_type: grantForm.principalType, principal_id: grantForm.principalId,
      permissions: [...grantForm.permissions], expires_at: null,
    });
    grantVisible.value = false;
    grantPage.value = 1;
    await loadGrants();
    ElMessage.success("资源授权已保存");
  } catch (error) {
    ElMessage.error(message(error, "资源授权保存失败"));
  } finally {
    saving.value = false;
  }
}

async function removeGrant(grant: ResourceGrantRecord) {
  if (!await confirmed("确定删除该资源授权吗？", "删除授权")) return;
  try {
    await api.deleteResourceGrant(grant.id);
    await loadGrants();
  } catch (error) {
    ElMessage.error(message(error, "资源授权删除失败"));
  }
}

function openAllocation() {
  Object.assign(allocationForm, {
    principalType: "user", principalId: userPrincipals.value[0]?.id ?? "",
    resourcePoolId: pools.value[0]?.id ?? "", maxTrainingJobs: 1, maxGpuCount: 1, maxServiceInstances: 1, expiresAt: "",
  });
  allocationVisible.value = true;
}

function selectFirstAllocationPrincipal() {
  allocationForm.principalId = allocationPrincipals.value[0]?.id ?? "";
}

async function submitAllocation() {
  if (!allocationForm.principalId || !allocationForm.resourcePoolId) {
    ElMessage.warning("请选择分配对象和资源池");
    return;
  }
  saving.value = true;
  try {
    await api.upsertResourceAllocation({
      principal_type: allocationForm.principalType, principal_id: allocationForm.principalId,
      resource_pool_id: allocationForm.resourcePoolId,
      max_concurrent_training_jobs: allocationForm.maxTrainingJobs,
      max_gpu_count: allocationForm.maxGpuCount,
      max_service_instances: allocationForm.maxServiceInstances,
      expires_at: allocationForm.expiresAt || null,
    });
    allocationVisible.value = false;
    allocationPage.value = 1;
    await loadAllocations();
    ElMessage.success("资源配额已保存");
  } catch (error) {
    ElMessage.error(message(error, "资源配额保存失败"));
  } finally {
    saving.value = false;
  }
}

async function removeAllocation(allocation: ResourceAllocationRecord) {
  if (!await confirmed("确定删除该资源分配策略吗？", "删除配额")) return;
  try {
    await api.deleteResourceAllocation(allocation.id);
    await loadAllocations();
  } catch (error) {
    ElMessage.error(message(error, "资源配额删除失败"));
  }
}

function principalLabel(type: string, id: string) {
  if (type === "user") return userPrincipals.value.find((item) => item.id === id)?.label ?? id;
  if (type === "group") return groupPrincipals.value.find((item) => item.id === id)?.label ?? id;
  return "全部组织成员";
}

function poolLabel(id: string) {
  return pools.value.find((pool) => pool.id === id)?.name ?? id;
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
