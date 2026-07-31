<template>
  <article
    class="dataset-card data-asset-card"
    :class="mode === 'prepare' ? 'prepare-card' : 'library-card'"
    role="button"
    tabindex="0"
    @click="emit('select')"
    @keydown.enter.prevent="emit('select')"
    @keydown.space.prevent="emit('select')"
  >
    <div class="dataset-actions" @click.stop>
      <button
        class="dataset-more-button"
        type="button"
        :aria-label="`更多操作：${asset.name || asset.id}`"
        :data-testid="`dataset-more-${asset.id}`"
        @click="emit('toggle-menu')"
      >
        <MoreFilled />
      </button>
      <div v-if="menuOpen" class="dataset-action-menu">
        <button v-if="mode === 'datasets'" type="button" @click="emit('edit')">编辑</button>
        <button v-if="mode === 'datasets'" type="button" @click="emit('share')">公开配置</button>
        <button
          v-if="mode === 'prepare'"
          type="button"
          :data-testid="`convert-dataset-${asset.id}`"
          :disabled="converting"
          @click="emit('convert')"
        >
          {{ converting ? "转换中" : "转为数据集" }}
        </button>
        <button
          class="danger"
          type="button"
          :data-testid="`delete-dataset-${asset.id}`"
          :disabled="deleting"
          @click="emit('delete')"
        >
          删除
        </button>
      </div>
    </div>

    <template v-if="mode === 'prepare'">
      <div class="data-asset-card__layout prepare-card-layout">
        <div class="data-asset-card__title prepare-card-head">
          <span class="status-dot" aria-hidden="true"></span>
          <strong :title="asset.name || asset.id">{{ asset.name || asset.id }}</strong>
        </div>
        <div class="data-asset-card__chips chip-row">
          <span class="chip chip-success">✓ {{ statusText }}</span>
          <span class="chip chip-success">▣ {{ sourceText }}</span>
          <span class="chip">{{ taskText }}</span>
        </div>
        <div class="data-asset-card__metadata dataset-meta">
          <time>{{ formattedTime }}</time>
          <button
            v-if="labelStudioAvailable"
            class="data-asset-card__link"
            type="button"
            :data-testid="`label-studio-${asset.id}`"
            @click.stop="emit('label-studio')"
          >
            <Link />
            Label Studio
          </button>
        </div>
        <div class="data-asset-card__counts dataset-stats">
          <span>样本 {{ count(asset.sample_count) }}</span>
          <span>标注 {{ count(asset.annotation_count) }}</span>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="data-asset-card__layout data-asset-card__layout--published">
        <header class="data-asset-card__title library-card-head">
          <strong :title="asset.name || asset.id">{{ asset.name || asset.id }}</strong>
          <span>{{ taskText }}</span>
        </header>
        <div class="data-asset-card__chips library-tags">
          <span>{{ sourceText }}</span>
          <span>{{ statusText }}</span>
        </div>
        <p class="data-asset-card__summary">{{ taskText }}数据集，共 {{ count(asset.sample_count) }} 个样本</p>
        <div class="data-asset-card__metadata">
          <time>{{ formattedTime }}</time>
          <span>{{ visibilityText }}</span>
        </div>
        <div class="data-asset-card__counts dataset-stats">
          <span>标注 {{ count(asset.annotation_count) }}</span>
          <button
            v-if="canValidate"
            class="dataset-inline-action"
            type="button"
            :data-testid="`validate-dataset-${asset.id}`"
            :disabled="validating"
            @click.stop="emit('validate')"
          >
            {{ validateText }}
          </button>
          <button
            class="dataset-inline-action"
            type="button"
            :data-testid="`process-dataset-${asset.id}`"
            @click.stop="emit('process')"
          >
            数据处理
          </button>
        </div>
      </div>
    </template>
  </article>
</template>

<script setup lang="ts">
import { Link, MoreFilled } from "@element-plus/icons-vue";

type DataAsset = {
  id: string;
  name?: string;
  sample_count?: number;
  annotation_count?: number;
  visibility?: string;
  asset_role?: string;
};

const props = withDefaults(
  defineProps<{
    asset: DataAsset;
    mode: "prepare" | "datasets";
    statusText: string;
    sourceText: string;
    taskText: string;
    formattedTime: string;
    labelStudioAvailable?: boolean;
    menuOpen?: boolean;
    converting?: boolean;
    deleting?: boolean;
    validating?: boolean;
    canValidate?: boolean;
    validateText?: string;
  }>(),
  {
    labelStudioAvailable: false,
    menuOpen: false,
    converting: false,
    deleting: false,
    validating: false,
    canValidate: false,
    validateText: "检验",
  },
);

const emit = defineEmits<{
  select: [];
  "toggle-menu": [];
  edit: [];
  share: [];
  convert: [];
  delete: [];
  "label-studio": [];
  validate: [];
  process: [];
}>();

const visibilityText = props.asset.visibility === "public" ? "公开" : props.asset.visibility === "organization" ? "组织内" : "私有";

function count(value?: number) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}
</script>

<style scoped>
.data-asset-card {
  background: var(--visiox-card-surface);
  border: 1px solid var(--visiox-card-border);
  border-radius: var(--visiox-card-radius);
  box-sizing: border-box;
  cursor: pointer;
  min-height: 138px;
  padding: 18px 18px 16px;
  position: relative;
  text-align: left;
  transition: border-color 0.15s ease;
}

.data-asset-card:hover {
  border-color: #aeb7c3;
  box-shadow: none;
  transform: none;
}

.prepare-card {
  height: 220px;
}

.library-card {
  min-height: 122px;
  padding: 16px 16px 14px;
}

.data-asset-card__layout {
  display: grid;
  grid-template-rows: 26px 58px 24px 20px;
  row-gap: 8px;
}

.data-asset-card__layout--published {
  grid-template-rows: 28px 24px 36px 22px 26px;
  row-gap: 6px;
}

.data-asset-card__title {
  align-items: center;
  display: flex;
  gap: 10px;
  min-width: 0;
}

.data-asset-card__title strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-dot {
  background: #22c55e;
  border-radius: 50%;
  flex: 0 0 auto;
  height: 8px;
  width: 8px;
}

.data-asset-card__chips {
  align-content: flex-start;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  overflow: hidden;
}

.chip,
.library-tags span {
  background: #f1f5f9;
  border-radius: 4px;
  color: #334155;
  font-size: 12px;
  line-height: 24px;
  max-width: 100%;
  overflow: hidden;
  padding: 0 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-success {
  background: #dcfce7;
  color: #15803d;
}

.data-asset-card__metadata,
.data-asset-card__counts {
  align-items: center;
  color: #64748b;
  display: flex;
  font-size: 13px;
  gap: 12px;
  min-width: 0;
}

.data-asset-card__metadata time {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.data-asset-card__link,
.dataset-inline-action {
  align-items: center;
  background: transparent;
  border: 0;
  color: #2563eb;
  cursor: pointer;
  display: inline-flex;
  gap: 4px;
  padding: 0;
  white-space: nowrap;
}

.data-asset-card__link svg {
  height: 14px;
  width: 14px;
}

.data-asset-card__summary {
  color: #475569;
  font-size: 13px;
  line-height: 18px;
  margin: 0;
  overflow: hidden;
}

.dataset-actions {
  position: absolute;
  right: 10px;
  top: 10px;
  z-index: 2;
}

.dataset-more-button {
  align-items: center;
  background: #f8fafc;
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  color: #475569;
  display: inline-flex;
  height: 28px;
  justify-content: center;
  opacity: 0;
  padding: 0;
  pointer-events: none;
  width: 28px;
}

.data-asset-card:hover .dataset-more-button,
.data-asset-card:focus-within .dataset-more-button {
  opacity: 1;
  pointer-events: auto;
}

.dataset-more-button svg {
  height: 18px;
  width: 18px;
}

.dataset-action-menu {
  background: #ffffff;
  border: 1px solid #dbe3ef;
  border-radius: 4px;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.14);
  min-width: 128px;
  padding: 6px;
  position: absolute;
  right: 0;
  top: 38px;
}

.dataset-action-menu button {
  background: transparent;
  border: 0;
  border-radius: 4px;
  color: #334155;
  cursor: pointer;
  display: block;
  padding: 8px 10px;
  text-align: left;
  width: 100%;
}

.dataset-action-menu button:hover {
  background: #f1f5f9;
}

.dataset-action-menu .danger {
  color: #dc2626;
}

@media (max-width: 720px) {
  .data-asset-card {
    height: auto;
    min-height: 190px;
  }
}
</style>
