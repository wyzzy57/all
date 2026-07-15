import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TrainingMetricCard from "@/components/training/TrainingMetricCard.vue";

const MetricLineChartStub = {
  name: "MetricLineChart",
  props: ["series", "unit", "height", "axisMin", "axisMax", "valueFormat", "smoothing"],
  template: '<div data-testid="chart-stub">{{ Object.keys(series).join(",") }}</div>',
};

describe("TrainingMetricCard", () => {
  it("renders raw latest and best values from its primary metric", () => {
    const wrapper = mount(TrainingMetricCard, {
      props: {
        card: {
          id: "box-loss",
          title: "Box Loss",
          unit: "Loss",
          format: "number" as const,
          direction: "min" as const,
          canonicalKeys: ["train.box_loss", "val.box_loss"],
          primarySeries: "Validation",
          series: {
            Train: [{ step: 1, value: 1.2, timestamp: 100 }],
            Validation: [
              { step: 1, value: 1.4, timestamp: 100 },
              { step: 2, value: 0.8, timestamp: 110 },
              { step: 3, value: 0.9, timestamp: 120 },
            ],
          },
        },
        smoothing: 0.4,
      },
      global: { stubs: { MetricLineChart: MetricLineChartStub } },
    });

    expect(wrapper.get("[data-testid='metric-card-title']").text()).toBe("Box Loss");
    expect(wrapper.get("[data-testid='metric-card-latest']").text()).toContain("0.9000");
    expect(wrapper.get("[data-testid='metric-card-best']").text()).toContain("0.8000");
    expect(wrapper.get("[data-testid='chart-stub']").text()).toBe("Train,Validation");
    expect(wrapper.getComponent(MetricLineChartStub).props("smoothing")).toBe(0.4);
  });

  it("formats score summaries as ratios instead of mixing them with percentages", () => {
    const wrapper = mount(TrainingMetricCard, {
      props: {
        card: {
          id: "map50",
          title: "mAP50",
          unit: "Score",
          format: "ratio" as const,
          direction: "max" as const,
          axis: { min: 0, max: 1 },
          canonicalKeys: ["metrics.map50"],
          primarySeries: "mAP50",
          series: { mAP50: [{ step: 1, value: 0.70123, timestamp: 100 }] },
        },
        smoothing: 0,
      },
      global: { stubs: { MetricLineChart: MetricLineChartStub } },
    });

    expect(wrapper.get("[data-testid='metric-card-latest']").text()).toContain("0.7012");
    expect(wrapper.getComponent(MetricLineChartStub).props("axisMin")).toBe(0);
    expect(wrapper.getComponent(MetricLineChartStub).props("axisMax")).toBe(1);
  });
});
