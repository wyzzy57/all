import { ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePipelineWizardDraft } from "@/features/pipeline-wizard/usePipelineWizardDraft";

describe("usePipelineWizardDraft", () => {
  beforeEach(() => vi.useFakeTimers());

  it("saves a changed enabled draft after the debounce window", async () => {
    const draft = ref({ name: "before" });
    const enabled = ref(true);
    const save = vi.fn().mockResolvedValue(undefined);
    const state = usePipelineWizardDraft(draft, enabled, save, { delay: 500 });

    draft.value.name = "after";
    await vi.advanceTimersByTimeAsync(499);
    expect(save).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);

    expect(save).toHaveBeenCalledTimes(1);
    expect(state.status.value).toBe("saved");
  });

  it("keeps disabled drafts local and exposes save failures", async () => {
    const draft = ref({ name: "before" });
    const enabled = ref(false);
    const save = vi.fn().mockRejectedValue(new Error("offline"));
    const state = usePipelineWizardDraft(draft, enabled, save, { delay: 500 });

    draft.value.name = "local-only";
    await vi.advanceTimersByTimeAsync(600);
    expect(save).not.toHaveBeenCalled();

    enabled.value = true;
    await expect(state.saveNow()).rejects.toThrow("offline");
    expect(state.status.value).toBe("error");
  });

  it("autosaves a selected framework and model as one draft value", async () => {
    const draft = ref({ framework: "ultralytics", modelKey: "yolo26-n" });
    const save = vi.fn().mockResolvedValue(undefined);
    usePipelineWizardDraft(draft, ref(true), save, { delay: 500 });

    draft.value = { framework: "paddlex", modelKey: "pp-yoloe-s" };
    await vi.advanceTimersByTimeAsync(500);

    expect(save).toHaveBeenCalledTimes(1);
    expect(draft.value).toEqual({ framework: "paddlex", modelKey: "pp-yoloe-s" });
  });
});
