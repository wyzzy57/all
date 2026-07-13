<script setup lang="ts">
import { Download } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, onMounted, ref } from "vue";

import { api, type TrainingArtifactRecord } from "@/api/client";

type ArtifactGroupId = "results" | "curves" | "confusion" | "batches";

type ArtifactView = TrainingArtifactRecord & {
  url: string;
};

const props = defineProps<{
  jobId: string;
}>();

const groupDefinitions: Array<{ id: ArtifactGroupId; label: string }> = [
  { id: "results", label: "训练结果" },
  { id: "curves", label: "评估曲线" },
  { id: "confusion", label: "混淆矩阵" },
  { id: "batches", label: "训练批次" },
];

const artifacts = ref<ArtifactView[]>([]);
const loading = ref(false);

const groups = computed(() => groupDefinitions.map((definition) => ({
  ...definition,
  items: artifacts.value.filter((artifact) => artifactGroup(artifact) === definition.id),
})));

function artifactGroup(artifact: TrainingArtifactRecord): ArtifactGroupId {
  if (artifact.kind === "weight") return "results";
  const name = artifact.name.toLowerCase();
  if (name.includes("confusion_matrix")) return "confusion";
  if (name.includes("batch")) return "batches";
  return "curves";
}

function isPreviewable(name: string) {
  return /\.(?:avif|bmp|gif|jpe?g|png|webp)$/i.test(name);
}

function formatFileSize(size: number) {
  if (!Number.isFinite(size) || size < 0) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const digits = value >= 10 || Number.isInteger(value) ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unitIndex]}`;
}

async function loadArtifacts() {
  loading.value = true;
  try {
    const response = await api.listTrainingJobArtifacts(props.jobId);
    artifacts.value = response.items.map((artifact) => ({
      ...artifact,
      url: api.trainingJobArtifactDownloadUrl(props.jobId, artifact.kind, artifact.name),
    }));
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "训练产物加载失败");
  } finally {
    loading.value = false;
  }
}

onMounted(loadArtifacts);
</script>

<template>
  <div class="artifact-gallery">
    <div v-if="loading" class="artifact-state">正在加载训练产物...</div>
    <el-empty v-else-if="artifacts.length === 0" description="该训练暂无分析产物" />
    <section v-for="group in groups" v-else :key="group.id" class="artifact-group">
      <header class="artifact-group-heading">
        <strong>{{ group.label }}</strong>
        <span>{{ group.items.length }}</span>
      </header>
      <div v-if="group.items.length" class="artifact-grid">
        <article v-for="artifact in group.items" :key="`${artifact.kind}-${artifact.name}`" class="artifact-item">
          <a
            v-if="isPreviewable(artifact.name)"
            class="artifact-preview"
            :href="artifact.url"
            target="_blank"
            rel="noopener noreferrer"
          >
            <img
              class="artifact-preview-image"
              :data-testid="`artifact-preview-${artifact.name}`"
              :src="artifact.url"
              :alt="artifact.name"
            >
          </a>
          <div v-else class="artifact-file-mark" aria-hidden="true">{{ artifact.name.split(".").pop()?.toUpperCase() }}</div>
          <footer class="artifact-meta">
            <div>
              <strong :title="artifact.name">{{ artifact.name }}</strong>
              <span>{{ formatFileSize(artifact.size_bytes) }}</span>
            </div>
            <a
              class="artifact-download"
              :data-testid="`artifact-download-${artifact.name}`"
              :href="artifact.url"
              :download="artifact.name"
              :title="`下载 ${artifact.name}`"
            >
              <el-icon><Download /></el-icon>
            </a>
          </footer>
        </article>
      </div>
      <div v-else class="artifact-group-empty">暂无文件</div>
    </section>
  </div>
</template>

<style scoped>
.artifact-gallery, .artifact-gallery * { box-sizing: border-box; }
.artifact-state { display: grid; min-height: 180px; place-items: center; color: #667085; }
.artifact-group { border-bottom: 1px solid #e3e8ef; }
.artifact-group:last-child { border-bottom: 0; }
.artifact-group-heading { display: flex; align-items: center; justify-content: space-between; min-height: 48px; padding: 0 18px; background: #f8fafc; }
.artifact-group-heading span { color: #667085; font-size: 12px; font-variant-numeric: tabular-nums; }
.artifact-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; padding: 14px 18px 18px; }
.artifact-item { display: grid; min-width: 0; overflow: hidden; border: 1px solid #dfe5ef; border-radius: 6px; background: #fff; }
.artifact-preview { position: relative; display: block; width: 100%; aspect-ratio: 16 / 10; overflow: hidden; border-bottom: 1px solid #e3e8ef; background: #f8fafc; }
.artifact-preview-image { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
.artifact-file-mark { display: grid; width: 100%; aspect-ratio: 16 / 10; place-items: center; border-bottom: 1px solid #e3e8ef; background: #eef4ff; color: #1d4ed8; font-size: 22px; font-weight: 700; }
.artifact-meta { display: grid; grid-template-columns: minmax(0, 1fr) 34px; align-items: center; gap: 10px; min-height: 54px; padding: 8px 10px 8px 12px; }
.artifact-meta > div { display: grid; min-width: 0; gap: 3px; }
.artifact-meta strong { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.artifact-meta span { color: #667085; font-size: 11px; }
.artifact-download { display: grid; width: 32px; height: 32px; place-items: center; border: 1px solid #d7deea; border-radius: 4px; color: #344054; }
.artifact-download:hover { border-color: #2563eb; color: #1d4ed8; }
.artifact-group-empty { padding: 16px 18px; color: #98a2b3; font-size: 13px; }
@container training-view (max-width: 900px) {
  .artifact-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@container training-view (max-width: 420px) {
  .artifact-grid { grid-template-columns: minmax(0, 1fr); padding-right: 10px; padding-left: 10px; }
  .artifact-group-heading { padding: 0 12px; }
}
</style>
