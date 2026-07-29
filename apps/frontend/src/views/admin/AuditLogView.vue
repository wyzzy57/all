<template>
  <section class="audit-log-page">
    <header class="identity-page-header">
      <div>
        <h1>审计日志</h1>
        <p>查看组织内管理员操作与资源访问记录。</p>
      </div>
    </header>

    <form class="audit-filters" :aria-disabled="isDenied" @submit.prevent="applyFilters">
      <fieldset
        class="audit-filter-fields"
        data-testid="audit-filter-fields"
        :disabled="isDenied"
      >
        <label>操作用户
          <input v-model.trim="filters.actorUserId" data-testid="audit-filter-actor" autocomplete="off" placeholder="用户 ID" />
        </label>
        <label>资源类型
          <input v-model.trim="filters.resourceType" data-testid="audit-filter-resource-type" autocomplete="off" placeholder="如 service" />
        </label>
        <label>资源 ID
          <input v-model.trim="filters.resourceId" data-testid="audit-filter-resource-id" autocomplete="off" placeholder="资源 ID" />
        </label>
        <label>操作
          <input v-model.trim="filters.action" data-testid="audit-filter-action" autocomplete="off" placeholder="如 service.deploy" />
        </label>
        <label>结果
          <select v-model="filters.result" data-testid="audit-filter-result">
            <option value="">全部</option>
            <option value="success">成功</option>
            <option value="denied">拒绝</option>
            <option value="failed">失败</option>
          </select>
        </label>
        <label>开始时间
          <input v-model="filters.createdFrom" data-testid="audit-filter-from" type="datetime-local" />
        </label>
        <label>结束时间
          <input v-model="filters.createdTo" data-testid="audit-filter-to" type="datetime-local" />
        </label>
        <div class="filter-actions">
          <button class="secondary-button" data-testid="audit-filter-reset" type="button" @click="clearFilters">重置</button>
          <button class="primary-button" data-testid="audit-filter-submit" type="submit">查询</button>
        </div>
      </fieldset>
    </form>

    <AsyncState
      v-if="loading"
      state="loading"
      title="正在加载审计日志..."
      test-id="audit-loading"
    />
    <AsyncState
      v-else-if="error"
      :state="errorState"
      :title="error"
      retry-label="重新加载"
      @retry="loadLogs"
    />
    <AsyncState
      v-else-if="items.length === 0"
      state="empty"
      title="暂无审计记录"
      test-id="audit-empty"
    />
    <template v-else>
      <div class="audit-table-wrap">
        <table class="audit-table">
          <thead>
            <tr>
              <th>时间</th><th>操作用户</th><th>操作</th><th>资源</th><th>结果</th><th>元数据</th><th>详情</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in items" :key="entry.id">
              <td class="audit-time">{{ formatTime(entry.created_at) }}</td>
              <td>{{ entry.actor_user_id || "系统" }}</td>
              <td>{{ entry.action }}</td>
              <td>{{ formatResource(entry) }}</td>
              <td>
                <span :data-testid="`audit-result-${entry.id}`" :class="['audit-result', resultClass(entry.result)]">
                  {{ resultLabel(entry.result) }}
                </span>
              </td>
              <td>
                <span
                  :data-testid="`audit-metadata-${entry.id}`"
                  class="audit-metadata-preview"
                  :title="metadataText(entry)"
                >{{ metadataText(entry) }}</span>
              </td>
              <td><button :data-testid="`audit-detail-${entry.id}`" class="audit-detail-button" type="button" @click="selected = entry">查看</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="audit-pagination">
        <span>共 {{ total }} 条</span>
        <button
          class="secondary-button"
          data-testid="audit-page-prev"
          type="button"
          :disabled="page === 1 || loading"
          @click="previousPage"
        >上一页</button>
        <span aria-current="page">第 {{ page }} 页</span>
        <button
          class="secondary-button"
          data-testid="audit-page-next"
          type="button"
          :disabled="nextCursor === null || loading"
          @click="nextPage"
        >下一页</button>
      </div>
    </template>

    <el-dialog v-model="detailVisible" title="审计记录详情" width="min(760px, calc(100vw - 32px))" destroy-on-close>
      <dl v-if="selected" class="audit-detail-summary">
        <div><dt>时间</dt><dd>{{ formatTime(selected.created_at) }}</dd></div>
        <div><dt>操作</dt><dd>{{ selected.action }}</dd></div>
        <div><dt>资源</dt><dd>{{ formatResource(selected) }}</dd></div>
        <div><dt>结果</dt><dd>{{ resultLabel(selected.result) }}</dd></div>
      </dl>
      <pre v-if="selected" data-testid="audit-detail-json" class="audit-detail-json">{{ JSON.stringify(redactMetadata(selected.metadata_json), null, 2) }}</pre>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { api, type AuditLogRecord } from "@/api/client";
import AsyncState from "@/components/common/AsyncState.vue";

const pageSize = 20;
const page = ref(1);
const total = ref(0);
const cursorHistory = ref<Array<string | undefined>>([undefined]);
const nextCursor = ref<string | null>(null);
const items = ref<AuditLogRecord[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const errorState = ref<"denied" | "error">("error");
const selected = ref<AuditLogRecord | null>(null);
let requestGeneration = 0;

const filters = reactive({
  actorUserId: "",
  resourceType: "",
  resourceId: "",
  action: "",
  result: "",
  createdFrom: "",
  createdTo: "",
});

const detailVisible = computed({
  get: () => selected.value !== null,
  set: (visible: boolean) => { if (!visible) selected.value = null; },
});
const isDenied = computed(() => error.value !== null && errorState.value === "denied");

function queryParams() {
  return {
    actor_user_id: filters.actorUserId || undefined,
    resource_type: filters.resourceType || undefined,
    resource_id: filters.resourceId || undefined,
    action: filters.action || undefined,
    result: filters.result || undefined,
    created_from: toIso(filters.createdFrom),
    created_to: toExclusiveMinuteEnd(filters.createdTo),
    cursor: cursorHistory.value[page.value - 1],
    limit: pageSize,
  };
}

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function toExclusiveMinuteEnd(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? undefined
    : new Date(date.getTime() + 60_000).toISOString();
}

async function loadLogs(): Promise<void> {
  const generation = ++requestGeneration;
  loading.value = true;
  error.value = null;
  try {
    const response = await api.listAuditLogs(queryParams());
    if (generation !== requestGeneration) return;
    items.value = response.items;
    total.value = response.total;
    nextCursor.value = response.next_cursor;
  } catch (caught) {
    if (generation !== requestGeneration) return;
    errorState.value = errorStatus(caught) === 403 ? "denied" : "error";
    error.value = caught instanceof Error ? caught.message : "审计日志加载失败";
  } finally {
    if (generation === requestGeneration) loading.value = false;
  }
}

function applyFilters(): void {
  if (isDenied.value) return;
  resetPagination();
  void loadLogs();
}

function clearFilters(): void {
  if (isDenied.value) return;
  Object.assign(filters, {
    actorUserId: "", resourceType: "", resourceId: "", action: "", result: "", createdFrom: "", createdTo: "",
  });
  resetPagination();
  void loadLogs();
}

function resetPagination(): void {
  page.value = 1;
  cursorHistory.value = [undefined];
  nextCursor.value = null;
}

function previousPage(): void {
  if (page.value === 1) return;
  page.value -= 1;
  void loadLogs();
}

function nextPage(): void {
  if (nextCursor.value === null) return;
  cursorHistory.value = [
    ...cursorHistory.value.slice(0, page.value),
    nextCursor.value,
  ];
  page.value += 1;
  void loadLogs();
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

function formatResource(entry: AuditLogRecord): string {
  if (!entry.resource_type) return "-";
  return entry.resource_id ? `${entry.resource_type} / ${entry.resource_id}` : entry.resource_type;
}

function resultLabel(value: string): string {
  return ({ success: "成功", denied: "拒绝", failed: "失败" } as Record<string, string>)[value] ?? value;
}

function resultClass(value: string): string {
  return value === "failed" ? "is-failed" : value === "denied" ? "is-denied" : "is-success";
}

const sensitiveKey = /(password|token|secret|cookie|privatekey|authorization|credential|apikey|accesskey)/;

function isSensitiveKey(key: string): boolean {
  return sensitiveKey.test(key.toLowerCase().replace(/[^a-z0-9]/g, ""));
}

function redactMetadata(value: unknown, seen = new WeakSet<object>()): unknown {
  if (Array.isArray(value)) return value.map((item) => redactMetadata(item, seen));
  if (!value || typeof value !== "object") return value;
  if (seen.has(value)) return "[CIRCULAR]";
  seen.add(value);
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    isSensitiveKey(key) ? "[REDACTED]" : redactMetadata(item, seen),
  ]));
}

function metadataText(entry: AuditLogRecord): string {
  return JSON.stringify(redactMetadata(entry.metadata_json));
}

function errorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== "object" || !("status" in error)) return undefined;
  return typeof error.status === "number" ? error.status : undefined;
}

onMounted(() => { void loadLogs(); });
</script>

<style scoped>
.audit-log-page { min-width: 0; container-type: inline-size; }
.identity-page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 20px; }
.identity-page-header h1 { margin: 0; font-size: 22px; line-height: 32px; color: #172033; }
.identity-page-header p { margin: 4px 0 0; color: #667085; font-size: 13px; }
.audit-filters { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px 16px; align-items: end; margin-bottom: 16px; padding: 16px; border: 1px solid #e3e8f1; border-radius: 6px; background: #fff; }
.audit-filter-fields { display: contents; margin: 0; padding: 0; border: 0; }
.audit-filters label { display: grid; gap: 6px; min-width: 0; color: #344054; font-size: 13px; font-weight: 500; }
.audit-filters input,.audit-filters select { box-sizing: border-box; width: 100%; min-width: 0; height: 34px; padding: 0 10px; border: 1px solid #d0d9e8; border-radius: 4px; background: #fff; color: #172033; font: inherit; }
.audit-filters input:focus,.audit-filters select:focus { outline: 2px solid #a8c8ff; outline-offset: 1px; border-color: #2f76ff; }
.filter-actions { display: flex; gap: 8px; align-items: center; }
.primary-button,.secondary-button,.audit-detail-button { min-height: 34px; border-radius: 4px; padding: 0 12px; font: inherit; cursor: pointer; }
.filter-actions .primary-button,
.filter-actions .secondary-button { min-height: 44px; }
.primary-button { border: 1px solid #2f76ff; background: #2f76ff; color: #fff; }.secondary-button { border: 1px solid #d0d9e8; background: #fff; color: #344054; }.audit-detail-button { position: relative; min-width: 44px; min-height: 24px; padding: 0; border: 0; background: transparent; color: #1763ff; }
.audit-detail-button::before {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 44px;
  height: 44px;
  content: "";
  transform: translate(-50%, -50%);
}
.audit-pagination .secondary-button { min-height: 44px; }
.audit-table-wrap { overflow-x: auto; border: 1px solid #e3e8f1; border-radius: 6px; background: #fff; }.audit-table { width: 100%; min-width: 980px; border-collapse: collapse; table-layout: fixed; font-size: 13px; }.audit-table th,.audit-table td { box-sizing: border-box; padding: 11px 12px; border-bottom: 1px solid #edf0f5; text-align: left; vertical-align: middle; color: #344054; }.audit-table th { background: #f8fafc; color: #475467; font-weight: 600; white-space: nowrap; }.audit-table th:nth-child(1) { width: 172px; }.audit-table th:nth-child(2) { width: 130px; }.audit-table th:nth-child(3) { width: 160px; }.audit-table th:nth-child(4) { width: 180px; }.audit-table th:nth-child(5) { width: 88px; }.audit-table th:nth-child(7) { width: 64px; }.audit-time { font-variant-numeric: tabular-nums; white-space: nowrap; }.audit-metadata-preview { display: block; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: #667085; }.audit-result { display: inline-flex; align-items: center; min-height: 22px; padding: 0 8px; border-radius: 11px; font-size: 12px; font-weight: 600; }.audit-result.is-success { background: #e8f8ef; color: #087443; }.audit-result.is-denied { background: #fff3dc; color: #9a6700; }.audit-result.is-failed { background: #fef0f0; color: #b42318; }.audit-pagination { display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 14px; color: #667085; font-size: 13px; }.audit-detail-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 20px; margin: 0 0 16px; }.audit-detail-summary div { min-width: 0; }.audit-detail-summary dt { margin-bottom: 4px; color: #667085; font-size: 12px; }.audit-detail-summary dd { margin: 0; overflow-wrap: anywhere; color: #172033; }.audit-detail-json { box-sizing: border-box; max-height: min(50vh, 440px); width: 100%; margin: 0; overflow: auto; padding: 12px; border: 1px solid #e3e8f1; border-radius: 4px; background: #f8fafc; color: #172033; font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
@container (max-width: 900px) { .audit-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@container (max-width: 520px) { .audit-filters,.audit-detail-summary { grid-template-columns: minmax(0, 1fr); }.audit-pagination { align-items: flex-end; flex-direction: column; }.filter-actions { justify-content: flex-end; } }
</style>
