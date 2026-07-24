import { describe, expect, it } from "vitest";

import {
  buildMetricCards,
  canonicalMetricKey,
  smoothMetricPoints,
  selectCanonicalSeries,
  summarizeMetric,
} from "@/components/training/trainingMetricCatalog";

const point = (step: number, value: number) => ({ step, value, timestamp: step * 100 });

describe("training metric catalog", () => {
  it("normalizes Ultralytics B aliases to their canonical metric keys", () => {
    expect(canonicalMetricKey("metrics/precision(B)")).toBe("metrics.precision");
    expect(canonicalMetricKey("metrics.recallb")).toBe("metrics.recall");
    expect(canonicalMetricKey("metrics/mAP50(B)")).toBe("metrics.map50");
    expect(canonicalMetricKey("metrics.map50_95b")).toBe("metrics.map50_95");
  });

  it("deduplicates aliases and prefers canonical series", () => {
    const cards = buildMetricCards({
      "metrics.map50": [point(1, 0.7)],
      "metrics.map50b": [point(1, 0.6)],
    });

    expect(cards).toHaveLength(1);
    expect(cards[0].id).toBe("map50");
    expect(cards[0].series).toEqual({ Validation: [point(1, 0.7)] });
  });

  it("pairs train and validation lines only for the same loss", () => {
    const cards = buildMetricCards({
      "train.box_loss": [point(1, 1.2)],
      "val.box_loss": [point(1, 1.4)],
      "train.cls_loss": [point(1, 0.8)],
      "metrics.precision": [point(1, 0.72)],
    });

    expect(cards.find((card) => card.id === "box-loss")?.series).toEqual({
      Train: [point(1, 1.2)],
      Validation: [point(1, 1.4)],
    });
    expect(cards.find((card) => card.id === "cls-loss")?.series).toEqual({ Train: [point(1, 0.8)] });
    expect(cards.find((card) => card.id === "precision")?.axis).toEqual({ min: 0, max: 1 });
    expect(cards.find((card) => card.id === "precision")?.series).toEqual({
      Validation: [point(1, 0.72)],
    });
  });

  it("labels validation quality metrics and training-only learning rate by source", () => {
    const cards = buildMetricCards({
      "metrics.recall": [point(1, 0.8)],
      "metrics.map50": [point(1, 0.9)],
      "metrics.map50_95": [point(1, 0.7)],
      learning_rate: [point(1, 0.001)],
    });

    expect(cards.find((card) => card.id === "recall")?.series).toEqual({
      Validation: [point(1, 0.8)],
    });
    expect(cards.find((card) => card.id === "map50")?.series).toEqual({
      Validation: [point(1, 0.9)],
    });
    expect(cards.find((card) => card.id === "map50-95")?.series).toEqual({
      Validation: [point(1, 0.7)],
    });
    expect(cards.find((card) => card.id === "learning-rate")?.series).toEqual({
      Train: [point(1, 0.001)],
    });
  });

  it("creates a separate card for every unknown scalar", () => {
    const cards = buildMetricCards({
      "custom.temperature": [point(1, 34)],
      "custom.throughput": [point(1, 120)],
    });

    expect(cards.map((card) => card.id)).toEqual(["custom-temperature", "custom-throughput"]);
    expect(cards.every((card) => Object.keys(card.series).length === 1)).toBe(true);
  });

  it("smooths display values without mutating raw points", () => {
    const raw = [point(1, 1), point(2, 3), point(3, 5)];
    const smoothed = smoothMetricPoints(raw, 0.5);

    expect(smoothed.map((item) => item.displayValue)).toEqual([1, 2, 3.5]);
    expect(smoothed.map((item) => item.value)).toEqual([1, 3, 5]);
    expect(raw.map((item) => item.value)).toEqual([1, 3, 5]);
  });

  it("summarizes latest and best raw values with their steps", () => {
    const points = [point(1, 1.4), point(2, 0.9), point(3, 1.1)];

    expect(summarizeMetric(points, "min")).toEqual({ latest: 1.1, best: 0.9, bestStep: 2 });
    expect(summarizeMetric(points, "max")).toEqual({ latest: 1.1, best: 1.4, bestStep: 1 });
  });

  it("selects a canonical comparison series over its aliases", () => {
    expect(selectCanonicalSeries({
      "metrics.map50b": [point(1, 0.6)],
      "metrics.map50": [point(1, 0.7)],
    }, "metrics.map50")).toEqual([point(1, 0.7)]);
  });
});
