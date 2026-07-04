import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";

function mockJsonResponse(payload: unknown = {}) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(payload),
  } as Response);
}

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses backend route contracts for task actions", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => mockJsonResponse({ items: [], total: 0 }));

    await api.downloadBaseModel("base-1");
    await api.analyzeDataset("dataset-1");
    await api.validateDataset("dataset-1");
    await api.createTrainingJob("pipeline-1", {});

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/base-models/base-1/download",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/datasets/dataset-1/analyze",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/datasets/dataset-1/validate",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/pipelines/pipeline-1/jobs",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
