import type {
  TrainingObservabilityScalarPoint,
  TrainingObservabilitySourceAvailability,
} from "@/api/client";

export type LlmScalarSeries = Record<string, TrainingObservabilityScalarPoint[]>;
export type LlmObservabilityAvailability = Record<string, TrainingObservabilitySourceAvailability>;

export type LlmChartDefinition = {
  id: string;
  title: string;
  unit: string;
  series: LlmScalarSeries;
  sourceKeys: string[];
  axisMin?: number;
  axisMax?: number;
  valueFormat?: "number" | "ratio" | "scientific" | "percent";
};

export type LlmChartGroup = {
  id: "loss" | "optimization" | "throughput" | "runtime" | "progress";
  title: string;
  charts: LlmChartDefinition[];
};

export type LlmResourceGroup = {
  id: "node" | "gpu";
  title: string;
  charts: LlmChartDefinition[];
};

export type LlmObservabilitySummary = {
  job_id: string;
  pipeline_name: string;
  status: string;
  progress: Record<string, unknown>;
  timing: Record<string, unknown>;
  environment: Record<string, unknown>;
  latest_metrics: Record<string, number>;
  availability: LlmObservabilityAvailability;
};

export type LlmAnalysisFinding = {
  code: string;
  severity: string;
  title: string;
  message: string;
  metric_names: string[];
  step_range: number[] | null;
  observed_values: Record<string, number | null>;
};

export type LlmAnalysisResponse = {
  findings: LlmAnalysisFinding[];
  availability: LlmObservabilityAvailability;
};

export type LlmArtifact = {
  path: string;
  size_bytes: number;
  sha256: string;
  download_url?: string;
};

export type LlmArtifactsResponse = {
  items: LlmArtifact[];
  availability: LlmObservabilityAvailability;
};

type LineDefinition = { key: string; label: string };
type ChartTemplate = Omit<LlmChartDefinition, "series" | "sourceKeys"> & { lines: LineDefinition[] };
type GroupTemplate = Omit<LlmChartGroup, "charts"> & { charts: ChartTemplate[] };

const METRIC_GROUPS: GroupTemplate[] = [
  {
    id: "loss",
    title: "损失",
    charts: [{
      id: "loss",
      title: "训练与验证 Loss",
      unit: "Loss",
      lines: [
        { key: "loss", label: "训练 Loss" },
        { key: "eval_loss", label: "验证 Loss" },
      ],
    }],
  },
  {
    id: "optimization",
    title: "优化过程",
    charts: [
      {
        id: "learning-rate",
        title: "学习率",
        unit: "Learning rate",
        valueFormat: "scientific",
        lines: [{ key: "learning_rate", label: "Learning rate" }],
      },
      {
        id: "grad-norm",
        title: "梯度范数",
        unit: "Gradient norm",
        axisMin: 0,
        lines: [{ key: "grad_norm", label: "Gradient norm" }],
      },
    ],
  },
  {
    id: "throughput",
    title: "训练吞吐",
    charts: [
      {
        id: "tokens-per-second",
        title: "Token 吞吐",
        unit: "tokens/s",
        axisMin: 0,
        lines: [{ key: "tokens_per_second", label: "Tokens/s" }],
      },
      {
        id: "samples-per-second",
        title: "样本吞吐",
        unit: "samples/s",
        axisMin: 0,
        lines: [{ key: "samples_per_second", label: "Samples/s" }],
      },
    ],
  },
  {
    id: "runtime",
    title: "运行时间",
    charts: [{
      id: "runtime",
      title: "Runtime",
      unit: "seconds",
      axisMin: 0,
      lines: [{ key: "train_runtime", label: "Runtime" }],
    }],
  },
  {
    id: "progress",
    title: "训练进度",
    charts: [
      {
        id: "epoch",
        title: "Epoch 进度",
        unit: "Epoch",
        axisMin: 0,
        lines: [{ key: "epoch", label: "Epoch" }],
      },
      {
        id: "step",
        title: "Step 进度",
        unit: "Step",
        axisMin: 0,
        lines: [{ key: "step", label: "Step" }],
      },
    ],
  },
];

function normalizeKey(key: string) {
  return key.trim().toLowerCase().replace(/\//g, ".").replace(/-/g, "_");
}

function findSeries(series: LlmScalarSeries, key: string) {
  const entry = Object.entries(series).find(([name]) => normalizeKey(name) === key);
  return entry?.[1] ?? [];
}

function buildChart(template: ChartTemplate, input: LlmScalarSeries): LlmChartDefinition | null {
  const series: LlmScalarSeries = {};
  const sourceKeys: string[] = [];
  for (const line of template.lines) {
    const points = findSeries(input, line.key);
    if (!points.length) continue;
    series[line.label] = points;
    sourceKeys.push(line.key);
  }
  if (!sourceKeys.length) return null;
  const { lines: _lines, ...chart } = template;
  return { ...chart, series, sourceKeys };
}

export function buildLlmMetricCharts(series: LlmScalarSeries): LlmChartGroup[] {
  return METRIC_GROUPS.map((group) => ({
    id: group.id,
    title: group.title,
    charts: group.charts
      .map((chart) => buildChart(chart, series))
      .filter((chart): chart is LlmChartDefinition => chart !== null),
  })).filter((group) => group.charts.length > 0);
}

function singleSeriesChart(
  id: string,
  title: string,
  unit: string,
  key: string,
  label: string,
  points: TrainingObservabilityScalarPoint[],
): LlmChartDefinition {
  return { id, title, unit, series: { [label]: points }, sourceKeys: [key], axisMin: 0 };
}

function gpuCharts(series: LlmScalarSeries): LlmChartDefinition[] {
  const definitions = [
    { suffixes: ["utilization_percent"], id: "gpu-utilization", title: "GPU 利用率", unit: "%" },
    { suffixes: ["memory_used_mb"], id: "gpu-memory", title: "显存使用", unit: "MiB" },
    { suffixes: ["temperature_celsius"], id: "gpu-temperature", title: "GPU 温度", unit: "°C" },
    { suffixes: ["power_watts", "power_draw_watts"], id: "gpu-power", title: "GPU 功耗", unit: "W" },
  ];
  return definitions.flatMap((definition) => {
    const values: LlmScalarSeries = {};
    const keys: string[] = [];
    for (const [key, points] of Object.entries(series)) {
      const match = key.match(new RegExp(`^gpu\\.([^.]+)\\.(${definition.suffixes.join("|")})$`, "i"));
      if (!match || !points.length) continue;
      values[match[1]] = points;
      keys.push(key);
    }
    return keys.length ? [{
      id: definition.id,
      title: definition.title,
      unit: definition.unit,
      series: values,
      sourceKeys: keys,
      axisMin: 0,
      axisMax: definition.suffixes.includes("utilization_percent") ? 100 : undefined,
    }] : [];
  });
}

export function buildLlmResourceCharts(series: LlmScalarSeries): LlmResourceGroup[] {
  const nodeDefinitions = [
    [["system.cpu_percent"], "node-cpu", "CPU 使用率", "%"],
    [["system.memory_percent"], "node-memory", "内存使用率", "%"],
    [["system.disk_percent"], "node-disk", "磁盘使用率", "%"],
    [["system.network_bytes_received", "system.network_receive_bytes_per_second"], "node-network-in", "网络接收", "Bytes"],
    [["system.network_bytes_sent", "system.network_send_bytes_per_second"], "node-network-out", "网络发送", "Bytes"],
  ] as const;
  const nodeCharts = nodeDefinitions.flatMap(([keys, id, title, unit]) => {
    const key = keys.find((candidate) => findSeries(series, candidate).length > 0);
    const points = key ? findSeries(series, key) : [];
    return key && points.length ? [singleSeriesChart(id, title, unit, key, title, points)] : [];
  });
  const groups: LlmResourceGroup[] = [];
  if (nodeCharts.length) groups.push({ id: "node", title: "节点资源", charts: nodeCharts });
  const gpu = gpuCharts(series);
  if (gpu.length) groups.push({ id: "gpu", title: "逐 GPU 资源", charts: gpu });
  return groups;
}

export function unavailableSources(availability: LlmObservabilityAvailability) {
  const labels: Record<string, string> = {
    mlflow: "MLflow",
    tensorboard: "TensorBoard",
    progress: "训练进度",
    artifacts: "训练产物",
  };
  return Object.entries(availability)
    .filter(([, value]) => !value.available)
    .map(([key, value]) => ({ key, label: labels[key] ?? key, reason: value.reason }));
}

export function isCheckpointArtifact(path: string) {
  const normalized = path.toLowerCase();
  return normalized.includes("checkpoint-")
    || /(^|\/)(adapter_model|pytorch_model|model)([-_.].*)?\.(safetensors|bin|pt)$/.test(normalized);
}
