import type {
  TrainingObservabilityScalarPoint,
  TrainingObservabilityScalars,
} from "@/api/client";

export type MetricValueFormat = "number" | "ratio" | "scientific" | "percent";
export type MetricDirection = "min" | "max";

export type TrainingMetricCard = {
  id: string;
  title: string;
  unit?: string;
  format: MetricValueFormat;
  direction: MetricDirection;
  axis?: { min?: number; max?: number };
  series: TrainingObservabilityScalars["series"];
  primarySeries: string;
  canonicalKeys: string[];
};

export type SmoothedMetricPoint = TrainingObservabilityScalarPoint & {
  displayValue: number;
};

const ALIASES: Record<string, string> = {
  "metrics.precisionb": "metrics.precision",
  "metrics.recallb": "metrics.recall",
  "metrics.map50b": "metrics.map50",
  "metrics.map50_95b": "metrics.map50_95",
};

const COMPARISON_ALIASES: Record<string, string> = {
  "metrics.precision": "detection.precision",
  "metrics.precisionb": "detection.precision",
  "metrics.recall": "detection.recall",
  "metrics.recallb": "detection.recall",
  "metrics.map50": "detection.ap50",
  "metrics.map50b": "detection.ap50",
  "metrics.map50_95": "detection.ap",
  "metrics.map50_95b": "detection.ap",
  "bbox_map": "detection.ap",
  "bbox_map50": "detection.ap50",
  "bbox_map_50": "detection.ap50",
  "precision": "detection.precision",
  "recall": "detection.recall",
  "tokens_per_second": "runtime.throughput",
  "samples_per_second": "runtime.throughput",
  "throughput.samples_per_second": "runtime.throughput",
  "elapsed_seconds": "runtime.elapsed_seconds",
  "train_runtime": "runtime.elapsed_seconds",
  "gpu_memory_peak_mb": "resource.gpu_memory_peak_mb",
};

type CardDefinition = Omit<TrainingMetricCard, "series" | "primarySeries"> & {
  lines: Array<{ key: string; label: string }>;
  primaryKey: string;
};

const CARD_DEFINITIONS: CardDefinition[] = [
  lossDefinition("box-loss", "Box Loss", "box_loss"),
  lossDefinition("cls-loss", "Classification Loss", "cls_loss"),
  lossDefinition("dfl-loss", "Distribution Focal Loss", "dfl_loss"),
  qualityDefinition("precision", "Precision", "metrics.precision"),
  qualityDefinition("recall", "Recall", "metrics.recall"),
  qualityDefinition("map50", "mAP50", "metrics.map50"),
  qualityDefinition("map50-95", "mAP50-95", "metrics.map50_95"),
  {
    id: "learning-rate",
    title: "Learning Rate",
    unit: "LR",
    format: "scientific",
    direction: "min",
    canonicalKeys: ["learning_rate"],
    lines: [{ key: "learning_rate", label: "Train" }],
    primaryKey: "learning_rate",
  },
];

function lossDefinition(id: string, title: string, suffix: string): CardDefinition {
  return {
    id,
    title,
    unit: "Loss",
    format: "number",
    direction: "min",
    canonicalKeys: [`train.${suffix}`, `val.${suffix}`],
    lines: [
      { key: `train.${suffix}`, label: "Train" },
      { key: `val.${suffix}`, label: "Validation" },
    ],
    primaryKey: `val.${suffix}`,
  };
}

function qualityDefinition(id: string, title: string, key: string): CardDefinition {
  return {
    id,
    title,
    unit: "Score",
    format: "ratio",
    direction: "max",
    axis: { min: 0, max: 1 },
    canonicalKeys: [key],
    lines: [{ key, label: "Validation" }],
    primaryKey: key,
  };
}

function normalizeMetricKey(key: string) {
  return key
    .trim()
    .toLowerCase()
    .replace(/\//g, ".")
    .replace(/\(([^)]*)\)/g, "$1")
    .replace(/-/g, "_")
    .replace(/[^a-z0-9_.]+/g, "")
    .replace(/\.+/g, ".")
    .replace(/^[._]+|[._]+$/g, "");
}

export function canonicalMetricKey(key: string) {
  const normalized = normalizeMetricKey(key);
  return ALIASES[normalized] ?? normalized;
}

function comparisonMetricKey(key: string) {
  const normalized = normalizeMetricKey(key);
  return COMPARISON_ALIASES[normalized] ?? normalized;
}

function canonicalSeries(series: TrainingObservabilityScalars["series"]) {
  const result = new Map<string, TrainingObservabilityScalarPoint[]>();
  const canonicalSources = new Set(Object.keys(series).map(normalizeMetricKey));

  for (const [sourceKey, points] of Object.entries(series)) {
    const normalized = normalizeMetricKey(sourceKey);
    const canonical = canonicalMetricKey(sourceKey);
    const isAlias = normalized !== canonical;
    if (isAlias && canonicalSources.has(canonical)) continue;
    if (!result.has(canonical) || !isAlias) result.set(canonical, points);
  }
  return result;
}

export function buildMetricCards(series: TrainingObservabilityScalars["series"]): TrainingMetricCard[] {
  const normalized = canonicalSeries(series);
  const consumed = new Set<string>();
  const cards: TrainingMetricCard[] = [];

  for (const definition of CARD_DEFINITIONS) {
    const cardSeries: TrainingObservabilityScalars["series"] = {};
    for (const line of definition.lines) {
      const points = normalized.get(line.key);
      if (!points?.length) continue;
      cardSeries[line.label] = points;
      consumed.add(line.key);
    }
    const labels = Object.keys(cardSeries);
    if (!labels.length) continue;
    const primaryLine = definition.lines.find((line) => line.key === definition.primaryKey && cardSeries[line.label]);
    cards.push({
      id: definition.id,
      title: definition.title,
      unit: definition.unit,
      format: definition.format,
      direction: definition.direction,
      axis: definition.axis,
      canonicalKeys: definition.canonicalKeys,
      series: cardSeries,
      primarySeries: primaryLine?.label ?? labels[0],
    });
  }

  for (const [key, points] of [...normalized.entries()].sort(([left], [right]) => left.localeCompare(right))) {
    if (consumed.has(key) || !points.length) continue;
    const title = key.replace(/[._]+/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
    cards.push({
      id: key.replace(/[^a-z0-9]+/g, "-"),
      title,
      format: key.includes("utilization_percent") ? "percent" : "number",
      direction: "max",
      axis: key.includes("utilization_percent") ? { min: 0, max: 100 } : undefined,
      canonicalKeys: [key],
      series: { [key]: points },
      primarySeries: key,
    });
  }

  return cards;
}

export function smoothMetricPoints(
  points: TrainingObservabilityScalarPoint[],
  coefficient: number,
): SmoothedMetricPoint[] {
  const smoothing = Math.min(0.99, Math.max(0, coefficient));
  let previous: number | null = null;
  return points.map((point) => {
    const displayValue = previous === null
      ? point.value
      : smoothing * previous + (1 - smoothing) * point.value;
    previous = displayValue;
    return { ...point, displayValue };
  });
}

export function summarizeMetric(points: TrainingObservabilityScalarPoint[], direction: MetricDirection) {
  if (!points.length) return null;
  const best = points.reduce((current, candidate) => {
    if (direction === "min") return candidate.value < current.value ? candidate : current;
    return candidate.value > current.value ? candidate : current;
  });
  return {
    latest: points[points.length - 1].value,
    best: best.value,
    bestStep: best.step,
  };
}

export function aliasesForCanonicalKey(key: string) {
  if (key.startsWith("detection.") || key.startsWith("runtime.") || key.startsWith("resource.")) {
    return [...new Set([key, ...Object.entries(COMPARISON_ALIASES)
      .filter(([, target]) => target === key)
      .map(([alias]) => alias)])];
  }
  const canonical = canonicalMetricKey(key);
  return [canonical, ...Object.entries(ALIASES)
    .filter(([, target]) => target === canonical)
    .map(([alias]) => alias)];
}

export function selectCanonicalSeries(
  series: TrainingObservabilityScalars["series"],
  key: string,
) {
  if (key.startsWith("detection.") || key.startsWith("runtime.") || key.startsWith("resource.")) {
    const matching = Object.entries(series)
      .filter(([sourceKey]) => comparisonMetricKey(sourceKey) === key)
      .sort(([left], [right]) => left.localeCompare(right));
    return matching[0]?.[1] ?? [];
  }
  return canonicalSeries(series).get(canonicalMetricKey(key)) ?? [];
}
