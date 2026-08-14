import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import PaddleXTrainingAnalysis from "@/features/training-observability/paddlex/PaddleXTrainingAnalysis.vue";
import { buildPaddleXMetricGroups } from "@/features/training-observability/paddlex/paddlexMetricCatalog";

const point = (step: number, value: number) => ({ step, value, timestamp: 1_722_000_000 + step });

describe("PaddleX training observability", () => {
  it("separates optimization, COCO quality, model losses, samples, and evaluation report", () => {
    const groups = buildPaddleXMetricGroups({
      learning_rate: [point(1, 0.001)],
      bbox_map: [point(1, 0.43)],
      bbox_map_50: [point(1, 0.72)],
      loss_cls: [point(1, 0.8)],
      samples_per_second: [point(1, 14)],
      eval_images: [point(1, 120)],
    });

    expect(groups.map((group) => group.id)).toEqual([
      "optimization", "coco-quality", "model-losses", "samples", "evaluation-report",
    ]);
    expect(groups.every((group) => group.charts.every((chart) => Object.keys(chart.series).length === 1))).toBe(true);
    expect(groups.find((group) => group.id === "coco-quality")?.charts.map((chart) => chart.unit)).toEqual(["ratio", "ratio"]);
  });

  it("keeps artifacts in the dedicated tab and does not expose a second visualization tool", async () => {
    const wrapper = mount(PaddleXTrainingAnalysis, {
      props: {
        series: { bbox_map: [point(1, 0.43)] },
        analysis: { findings: [], availability: {} },
      },
      global: {
        stubs: {
          MetricLineChart: { props: ["series"], template: '<div data-testid="metric-line-chart">{{ Object.keys(series).join(",") }}</div>' },
          "el-empty": { props: ["description"], template: "<div>{{ description }}</div>" },
        },
      },
    });
    await flushPromises();

    expect(wrapper.find('[data-testid="paddlex-visualdl-link"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="paddlex-artifact-best_model.pdparams"]').exists()).toBe(false);
  });
});
