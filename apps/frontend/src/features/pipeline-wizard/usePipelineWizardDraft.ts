import {
  getCurrentScope,
  onScopeDispose,
  ref,
  toValue,
  watch,
  type MaybeRefOrGetter,
  type Ref,
} from "vue";

export type PipelineDraftStatus = "idle" | "dirty" | "saving" | "saved" | "error";

export function usePipelineWizardDraft<T extends object>(
  draft: Ref<T>,
  enabled: MaybeRefOrGetter<boolean>,
  save: () => Promise<void>,
  options: { delay?: number } = {},
) {
  const delay = options.delay ?? 500;
  const status = ref<PipelineDraftStatus>("idle");
  const error = ref("");
  const lastSavedAt = ref<Date | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let requestSequence = 0;

  function clearTimer() {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  }

  async function saveNow() {
    if (!toValue(enabled)) return;
    clearTimer();
    const requestId = ++requestSequence;
    status.value = "saving";
    error.value = "";
    try {
      await save();
      if (requestId !== requestSequence) return;
      status.value = "saved";
      lastSavedAt.value = new Date();
    } catch (cause) {
      if (requestId === requestSequence) {
        status.value = "error";
        error.value = cause instanceof Error ? cause.message : "草稿保存失败";
      }
      throw cause;
    }
  }

  function scheduleSave() {
    if (!toValue(enabled)) return;
    clearTimer();
    status.value = "dirty";
    timer = setTimeout(() => {
      void saveNow().catch(() => undefined);
    }, delay);
  }

  const stop = watch(draft, scheduleSave, { deep: true, flush: "post" });
  if (getCurrentScope()) {
    onScopeDispose(() => {
      clearTimer();
      stop();
    });
  }

  return { status, error, lastSavedAt, saveNow, stop };
}
