<template>
  <section class="log-viewer" aria-label="日志查看器">
    <header class="viewer-toolbar">
      <div class="filters">
        <el-input v-model="keyword" clearable placeholder="搜索日志" />
        <el-select v-model="sourceFilter" aria-label="来源筛选">
          <el-option label="全部来源" value="all" />
          <el-option v-for="source in sources" :key="source" :label="source" :value="source" />
        </el-select>
        <el-select v-model="levelFilter" aria-label="级别筛选">
          <el-option label="全部级别" value="all" />
          <el-option v-for="level in levels" :key="level" :label="level" :value="level" />
        </el-select>
      </div>
      <div class="commands">
        <button type="button" @click="following = !following">{{ following ? "暂停跟随" : "继续跟随" }}</button>
        <button type="button" :disabled="loading" @click="refresh">刷新</button>
        <button type="button" @click="download">下载</button>
      </div>
    </header>

    <div ref="viewport" class="log-viewport" @scroll="handleScroll">
      <button v-if="hasOlder" class="older-button" type="button" @click="loadOlder">加载更早日志</button>
      <p v-if="loading && lines.length === 0" class="empty-state">正在读取日志...</p>
      <p v-else-if="visibleLines.length === 0" class="empty-state">暂无匹配日志</p>
      <ol v-else class="log-lines">
        <li v-for="line in visibleLines" :key="line.key" :class="lineTone(line.level)">
          <time>{{ formatTime(line.timestamp) }}</time>
          <span class="source">{{ line.source || "system" }}</span>
          <span class="level">{{ line.level || "INFO" }}</span>
          <span class="message">{{ line.message }}</span>
        </li>
      </ol>
    </div>
    <footer class="viewer-status">
      <span>{{ lines.length }} 行</span>
      <span>{{ streamStatus }}</span>
      <span v-if="error" class="error">{{ error }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";

import { api, type LogLineRecord } from "@/api/client";

const props = defineProps<{ streamId: string }>();
const MAX_RENDERED_LINES = 5_000;
const POLL_INTERVAL_MS = 1_500;

type DisplayLine = {
  key: string;
  timestamp: string | null;
  source: string;
  level: string;
  message: string;
};

const lines = ref<DisplayLine[]>([]);
const cursor = ref<string | null>(null);
const loading = ref(false);
const following = ref(true);
const hasOlder = ref(false);
const streamStatus = ref("连接中");
const error = ref("");
const keyword = ref("");
const sourceFilter = ref("all");
const levelFilter = ref("all");
const viewport = ref<HTMLElement | null>(null);
let timer: number | undefined;
let lineSequence = 0;

const sources = computed(() => [...new Set(lines.value.map((line) => line.source).filter(Boolean))]);
const levels = computed(() => [...new Set(lines.value.map((line) => line.level).filter(Boolean))]);
const visibleLines = computed(() => {
  const term = keyword.value.trim().toLowerCase();
  return lines.value.filter((line) =>
    (sourceFilter.value === "all" || line.source === sourceFilter.value)
    && (levelFilter.value === "all" || line.level === levelFilter.value)
    && (!term || line.message.toLowerCase().includes(term)),
  );
});

onMounted(start);
onUnmounted(stop);
watch(() => props.streamId, start);

async function start() {
  stop();
  lines.value = [];
  cursor.value = null;
  error.value = "";
  await refresh();
  timer = window.setInterval(refresh, POLL_INTERVAL_MS);
}

function stop() {
  if (timer !== undefined) window.clearInterval(timer);
  timer = undefined;
}

async function refresh() {
  if (!props.streamId || loading.value) return;
  loading.value = true;
  try {
    const [metadata, page] = await Promise.all([
      api.getLogStream(props.streamId),
      api.getLogChunks(props.streamId, cursor.value),
    ]);
    append(page.lines);
    cursor.value = page.next_cursor;
    hasOlder.value = page.has_more;
    streamStatus.value = metadata.status === "open" ? "实时更新中" : "日志已结束";
    error.value = "";
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "日志读取失败";
    streamStatus.value = "连接中断，自动重试";
  } finally {
    loading.value = false;
  }
}

function append(entries: LogLineRecord[]) {
  if (!entries.length) return;
  lines.value.push(...entries.map(toDisplayLine));
  if (lines.value.length > MAX_RENDERED_LINES) {
    lines.value.splice(0, lines.value.length - MAX_RENDERED_LINES);
    hasOlder.value = true;
  }
  if (following.value) void scrollToBottom();
}

function toDisplayLine(entry: LogLineRecord): DisplayLine {
  lineSequence += 1;
  return {
    key: `${lineSequence}-${entry.timestamp || ""}`,
    timestamp: entry.timestamp || null,
    source: entry.source || "system",
    level: (entry.level || "INFO").toUpperCase(),
    message: typeof entry.message === "string" ? entry.message : JSON.stringify(entry.message),
  };
}

async function scrollToBottom() {
  await nextTick();
  if (viewport.value) viewport.value.scrollTop = viewport.value.scrollHeight;
}

function handleScroll() {
  const element = viewport.value;
  if (!element) return;
  following.value = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
}

function loadOlder() {
  lines.value = [];
  cursor.value = null;
  hasOlder.value = false;
  void refresh();
}

async function download() {
  try {
    const blob = await api.downloadLogStream(props.streamId);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${props.streamId}.log`;
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "日志下载失败";
  }
}

function formatTime(value: string | null) {
  if (!value) return "--:--:--";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleTimeString("zh-CN", { hour12: false });
}

function lineTone(level: string) {
  const normalized = level.toLowerCase();
  if (["error", "fatal", "critical"].includes(normalized)) return "danger";
  if (["warn", "warning"].includes(normalized)) return "warning";
  return "normal";
}
</script>

<style scoped>
.log-viewer { min-width: 0; border: 1px solid #dfe5ef; background: #fff; }
.viewer-toolbar, .viewer-status, .filters, .commands { display: flex; align-items: center; }
.viewer-toolbar { justify-content: space-between; gap: 16px; padding: 10px 12px; border-bottom: 1px solid #e8ecf2; }
.filters, .commands { gap: 8px; }
.filters :deep(.el-input) { width: 220px; }
.filters :deep(.el-select) { width: 132px; }
.commands button, .older-button { border: 0; background: transparent; color: #2678ff; cursor: pointer; }
.commands button:disabled { color: #98a2b3; cursor: default; }
.log-viewport { height: min(54vh, 520px); min-height: 280px; overflow: auto; background: #101828; color: #d0d5dd; font: 12px/1.65 Consolas, "Cascadia Mono", monospace; }
.log-lines { min-width: max-content; margin: 0; padding: 8px 0; list-style: none; }
.log-lines li { display: grid; grid-template-columns: 88px 92px 72px minmax(420px, 1fr); gap: 10px; min-height: 24px; padding: 2px 12px; white-space: pre-wrap; }
.log-lines li:hover { background: #1d2939; }
.log-lines time, .source { color: #98a2b3; }
.log-lines .level { color: #84adff; }
.log-lines .warning .level { color: #fec84b; }
.log-lines .danger { background: rgb(180 35 24 / 16%); }
.log-lines .danger .level, .viewer-status .error { color: #f97066; }
.empty-state { padding: 28px; text-align: center; color: #98a2b3; }
.older-button { display: block; margin: 8px auto; color: #84adff; }
.viewer-status { gap: 18px; min-height: 34px; padding: 0 12px; border-top: 1px solid #e8ecf2; color: #667085; font-size: 12px; }
@media not all {
  .viewer-toolbar, .filters { align-items: stretch; flex-direction: column; }
  .filters :deep(.el-input), .filters :deep(.el-select) { width: 100%; }
}
</style>
