import type { TrainingObservabilityScalarPoint } from "@/api/client";

export type PaddleXScalarSeries = Record<string, TrainingObservabilityScalarPoint[]>;

export type PaddleXMetricChart = {
  id: string;
  title: string;
  unit: string;
  series: PaddleXScalarSeries;
  axisMin?: number;
  axisMax?: number;
  valueFormat?: "number" | "ratio" | "scientific" | "percent";
};

export type PaddleXMetricGroup = {
  id: "optimization" | "coco-quality" | "model-losses" | "samples" | "evaluation-report";
  title: string;
  charts: PaddleXMetricChart[];
};

type Definition = Omit<PaddleXMetricChart, "series"> & { keys: string[] };

const GROUPS: Array<Omit<PaddleXMetricGroup, "charts"> & { charts: Definition[] }> = [
  { id: "optimization", title: "优化", charts: [{ id: "learning-rate", title: "Learning rate", unit: "LR", valueFormat: "scientific", keys: ["learning_rate", "lr"] }] },
  { id: "coco-quality", title: "COCO 质量", charts: [
    { id: "ap", title: "AP50-95", unit: "ratio", valueFormat: "ratio", axisMin: 0, axisMax: 1, keys: ["bbox_map", "map"] },
    { id: "ap50", title: "AP50", unit: "ratio", valueFormat: "ratio", axisMin: 0, axisMax: 1, keys: ["bbox_map_50", "bbox_map50", "map50"] },
    { id: "ap75", title: "AP75", unit: "ratio", valueFormat: "ratio", axisMin: 0, axisMax: 1, keys: ["bbox_map_75", "bbox_map75", "map75"] },
  ] },
  { id: "model-losses", title: "模型损失", charts: [] },
  { id: "samples", title: "样本", charts: [
    { id: "samples-per-second", title: "Samples/s", unit: "samples/s", axisMin: 0, keys: ["samples_per_second"] },
    { id: "images-per-second", title: "Images/s", unit: "images/s", axisMin: 0, keys: ["images_per_second"] },
  ] },
  { id: "evaluation-report", title: "评估报告", charts: [
    { id: "eval-images", title: "Evaluated images", unit: "images", axisMin: 0, keys: ["eval_images", "evaluation_images"] },
  ] },
];

function normalize(key: string) {
  return key.trim().toLowerCase().replace(/\//g, ".").replace(/-/g, "_");
}

function pointsFor(series: PaddleXScalarSeries, keys: string[]) {
  const match = Object.entries(series).find(([key, points]) => keys.includes(normalize(key)) && points.length);
  return match ? { key: match[0], points: match[1] } : null;
}

function chart(definition: Definition, series: PaddleXScalarSeries): PaddleXMetricChart | null {
  const match = pointsFor(series, definition.keys);
  return match ? { ...definition, series: { [definition.title]: match.points } } : null;
}

export function buildPaddleXMetricGroups(series: PaddleXScalarSeries): PaddleXMetricGroup[] {
  const lossCharts = Object.entries(series)
    .filter(([key, points]) => points.length && /(^|[._])loss([._]|$)/i.test(key))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, points]) => ({
      id: key.replace(/[^a-z0-9]+/gi, "-").toLowerCase(), title: key, unit: "loss", axisMin: 0,
      series: { [key]: points },
    }));
  return GROUPS.map((group) => ({
    id: group.id,
    title: group.title,
    charts: group.id === "model-losses"
      ? lossCharts
      : group.charts.map((definition) => chart(definition, series)).filter((item): item is PaddleXMetricChart => item !== null),
  })).filter((group) => group.charts.length > 0);
}
