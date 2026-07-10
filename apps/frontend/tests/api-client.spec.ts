import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";

function mockJsonResponse(payload: unknown = {}) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(payload),
  } as Response);
}

function mockErrorResponse(payload: unknown, status = 503) {
  return Promise.resolve({
    ok: false,
    status,
    text: () => Promise.resolve(JSON.stringify(payload)),
  } as Response);
}

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses backend route contracts for task actions", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => mockJsonResponse({ items: [], total: 0 }));

    await api.createDataset({ name: "folder-a", task: "detect", class_schema: { names: ["ok", "defect"] } });
    await api.uploadDatasetSample("dataset-1", new File(["image"], "part.png", { type: "image/png" }));
    await api.uploadDatasetBatch("dataset-1", [new File(["label"], "part.txt", { type: "text/plain" })]);
    await api.analyzeDataset("dataset-1");
    await api.processDataset("dataset-1", { augment: { horizontalFlip: true }, clean: { blur: true } });
    await api.validateDataset("dataset-1");
    await api.createLabelProject("dataset-1", { external_project_id: "9001" });
    await api.syncLabelProjectSamples("label-project-1");
    await api.importLabelProjectAnnotations("label-project-1");
    await api.deleteDataset("dataset-1");
    await api.createTrainingJob("pipeline-1", {});

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/datasets",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/datasets/dataset-1/samples:upload",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
        headers: undefined,
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/datasets/dataset-1/samples:upload-batch",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
        headers: undefined,
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/datasets/dataset-1/analyze",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      "/datasets/dataset-1/process",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      "/datasets/dataset-1/validate",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      7,
      "/datasets/dataset-1/label-projects",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      8,
      "/label-projects/label-project-1/sync-samples",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      9,
      "/label-projects/label-project-1/import-annotations",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      10,
      "/datasets/dataset-1",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      11,
      "/pipelines/pipeline-1/jobs",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("surfaces backend detail messages from failed responses", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      mockErrorResponse({ detail: "Label Studio 服务不可用，请确认服务已启动并可访问" }),
    );

    await expect(api.createLabelProject("dataset-1")).rejects.toThrow("Label Studio 服务不可用");
  });
});
