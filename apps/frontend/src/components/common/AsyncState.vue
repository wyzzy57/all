<template>
  <section
    ref="region"
    :class="['async-state', `async-state--${state}`]"
    :data-testid="testId"
    :role="regionRole"
    :aria-live="liveMode"
    :aria-busy="state === 'loading' ? 'true' : undefined"
    aria-atomic="true"
    :tabindex="requiresAttention ? -1 : undefined"
  >
    <div class="async-state__content">
      <component :is="stateIcon" class="async-state__icon" aria-hidden="true" />
      <h2 class="async-state__title">{{ resolvedTitle }}</h2>
      <p v-if="description" class="async-state__description">{{ description }}</p>
      <button
        v-if="state === 'error'"
        class="async-state__retry"
        type="button"
        @click="emit('retry')"
      >
        <RefreshRight aria-hidden="true" />
        <span>{{ retryLabel }}</span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import {
  CircleCloseFilled,
  InfoFilled,
  Loading,
  Lock,
  RefreshRight,
  WarningFilled,
} from "@element-plus/icons-vue";
import { computed, nextTick, ref, watch } from "vue";

export type AsyncStateKind = "loading" | "empty" | "denied" | "error" | "terminal";

const props = withDefaults(defineProps<{
  state: AsyncStateKind;
  title?: string;
  description?: string;
  retryLabel?: string;
  testId?: string;
  focusOnAttention?: boolean;
}>(), {
  title: "",
  description: "",
  retryLabel: "重试",
  testId: undefined,
  focusOnAttention: false,
});

const emit = defineEmits<{
  retry: [];
}>();

const region = ref<HTMLElement | null>(null);
const requiresAttention = computed(() => ["denied", "error", "terminal"].includes(props.state));
const regionRole = computed(() => requiresAttention.value ? "alert" : "status");
const liveMode = computed(() => requiresAttention.value ? "assertive" : "polite");
const stateIcon = computed(() => ({
  loading: Loading,
  empty: InfoFilled,
  denied: Lock,
  error: WarningFilled,
  terminal: CircleCloseFilled,
})[props.state]);
const resolvedTitle = computed(() => props.title || ({
  loading: "正在加载",
  empty: "暂无数据",
  denied: "无权访问",
  error: "加载失败",
  terminal: "操作无法继续",
})[props.state]);

watch(
  [() => props.state, () => props.focusOnAttention],
  async ([state, focusOnAttention]) => {
    if (!focusOnAttention || !["denied", "error", "terminal"].includes(state)) return;
    await nextTick();
    region.value?.focus({ preventScroll: true });
  },
  { immediate: true, flush: "post" },
);
</script>
