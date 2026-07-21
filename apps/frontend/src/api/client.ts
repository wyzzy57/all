export type ListResponse<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type BaseModelRecord = {
  id: string;
  family: string;
  task: string;
  scale: string;
  filename: string;
  status: string;
  local_uri?: string | null;
};

export type DatasetRecord = {
  id: string;
  name: string;
  task: string;
  status: string;
  class_schema?: { names?: unknown[] };
  sample_count: number;
  annotation_count: number;
  source?: string;
  created_at?: string;
  updated_at?: string;
};

export type DatasetSampleRecord = {
  id: string;
  dataset_id: string;
  file_uri: string;
  width?: number | null;
  height?: number | null;
  checksum?: string | null;
  split?: string | null;
  annotation_status: string;
  created_at?: string;
  updated_at?: string;
};

export type SampleUploadResponse = {
  created_count: number;
  duplicate_count: number;
  skipped_count: number;
  samples: DatasetSampleRecord[];
};

export type LabelProjectRecord = {
  id: string;
  dataset_id: string;
  provider: string;
  external_project_id?: string | null;
  project_url?: string | null;
  sync_status: string;
  last_sync_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type TrainingPipelineRecord = {
  id: string;
  name: string;
  task: string;
  scale: string;
  status: string;
  base_model_id?: string | null;
  dataset_id?: string | null;
  params_template?: Record<string, unknown>;
  default_environment?: Record<string, unknown>;
  is_public?: boolean;
  public_scope?: Record<string, unknown>;
  is_favorite?: boolean;
  created_at?: string;
  updated_at?: string;
};

export type TrainingJobRecord = {
  id: string;
  pipeline_id: string;
  status: string;
  task_id?: string | null;
  trained_model_id?: string | null;
  environment?: Record<string, unknown>;
  params?: Record<string, unknown>;
  metrics: Record<string, unknown>;
  log_uri?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type TrainingArtifactRecord = {
  name: string;
  kind: "weight" | "visualization";
  size_bytes: number;
  download_url: string;
};

export type TrainingObservabilitySourceAvailability = {
  available: boolean;
  reason: string | null;
};

export type TrainingObservabilityAvailability = Record<string, TrainingObservabilitySourceAvailability>;

export type TrainingObservabilityScalarPoint = {
  step: number;
  value: number;
  timestamp: number;
};

export type TrainingObservabilityScalars = {
  series: Record<string, TrainingObservabilityScalarPoint[]>;
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilityResources = {
  series: Record<string, TrainingObservabilityScalarPoint[]>;
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilityGraphNode = {
  id: string;
  label: string;
  op: string;
  attributes: Record<string, unknown>;
};

export type TrainingObservabilityGraphEdge = {
  source: string;
  target: string;
};

export type TrainingObservabilityGraph = {
  nodes: TrainingObservabilityGraphNode[];
  edges: TrainingObservabilityGraphEdge[];
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilityHistogramBucket = {
  lower: number;
  upper: number;
  count: number;
};

export type TrainingObservabilityHistogram = {
  kind: "weight" | "gradient";
  tag: string;
  step: number;
  buckets: TrainingObservabilityHistogramBucket[];
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilitySummary = {
  job_id: string;
  pipeline_id: string;
  pipeline_name: string;
  status: string;
  progress: Record<string, unknown>;
  timing: Record<string, unknown>;
  environment: Record<string, unknown>;
  latest_metrics: Record<string, number>;
  available_scalar_keys: string[];
  available_histograms: Record<"weight" | "gradient", string[]>;
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilityRangeParams = {
  start_step?: number;
  end_step?: number;
  max_points?: number;
};

export type TrainingObservabilityScalarsParams = TrainingObservabilityRangeParams & {
  keys: string[];
};

export type TrainingObservabilityHistogramParams = {
  kind: "weight" | "gradient";
  tag: string;
  step: number;
};

export type TrainedModelRecord = {
  id: string;
  pipeline_id?: string | null;
  training_job_id?: string | null;
  name: string;
  version: string;
  task: string;
  artifact_uri: string;
  metrics: Record<string, unknown>;
  status: string;
  created_at?: string;
  updated_at?: string;
};

export type PipelinePredictResponse = {
  pipeline_id: string;
  model_weight: string;
  environment: string;
  predictions: Array<Record<string, unknown>>;
  result_image: string;
};

export type PipelineEvaluationResponse = {
  id: string;
  pipeline_id: string;
  dataset_id: string;
  evaluation_set: string;
  model_weight: string;
  environment: string;
  status: string;
  score?: number | null;
  metrics: Record<string, unknown>;
  created_at?: string;
};

export type DeploymentServiceRecord = {
  id: string;
  name: string;
  pipeline_id: string;
  trained_model_id?: string | null;
  model_name: string;
  model_weight: string;
  environment: string;
  instance_count: number;
  instance_name: string;
  resource_summary: string;
  status: string;
  endpoint: string;
  calls: number;
  config: Record<string, unknown>;
  instance_id?: string | null;
  node_id?: string | null;
  container_id?: string | null;
  image_digest?: string | null;
  model_checksum?: string | null;
  engine?: string | null;
  engine_digest?: string | null;
  port?: number | null;
  health_status?: string | null;
  task_id?: string | null;
  remote_execution_id?: string | null;
  phase?: string | null;
  log_uri?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
};

export type ResourcePoolRecord = {
  id: string;
  name: string;
  kind: string;
  selector: Record<string, unknown>;
  compatibility_policy: Record<string, unknown>;
  enabled: boolean;
};

export type ComputeNodeRecord = {
  id: string;
  name: string;
  resource_pool_id?: string | null;
  status: string;
  architecture: string;
  platform_kind: string;
  capabilities: Record<string, unknown>;
  resources: Record<string, unknown>;
  fingerprint: Record<string, unknown>;
  agent_version: string;
  certificate_expires_at?: string | null;
  last_seen_at?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type ServiceCreatePayload = {
  name: string;
  pipeline_id: string;
  trained_model_id: string;
  model_name: string;
  model_weight: string;
  environment: string;
  instance_name: string;
  resource_summary: string;
  node_id: string;
  image_digest: string;
  model_checksum: string;
  port?: number;
  format?: "auto" | "pt" | "onnx" | "engine";
  precision?: "auto" | "fp32" | "fp16" | "int8";
  input_shape?: [number, number, number, number];
  gpu_uuids?: string[];
  calibration_dataset_uri?: string;
  config?: Record<string, unknown>;
};

export type TaskRecord = {
  id: string;
  task_type: string;
  status: string;
  progress: number;
  resource_type?: string | null;
  resource_id?: string | null;
  stage?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable: boolean;
  payload?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: isFormData
      ? init?.headers
      : {
          "Content-Type": "application/json",
          ...(init?.headers ?? {})
        },
    ...init
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(readErrorDetail(detail) || `HTTP ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function requestText(url: string): Promise<string> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`日志读取失败（HTTP ${response.status}）`);
  return response.text();
}

function readErrorDetail(text: string): string {
  if (!text) return "";
  try {
    const payload = JSON.parse(text) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) return payload.detail.map((item) => JSON.stringify(item)).join("; ");
  } catch {
    // Plain-text API errors are already readable.
  }
  return text;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  });
  const value = search.toString();
  return value ? `?${value}` : "";
}

export const api = {
  listBaseModels: (params: { task?: string; status?: string } = {}) =>
    request<ListResponse<BaseModelRecord>>(`/base-models${query(params)}`),
  listTrainedModels: (params: { task?: string; pipeline_id?: string; status?: string; limit?: number; offset?: number } = {}) =>
    request<ListResponse<TrainedModelRecord>>(`/trained-models${query(params)}`),
  markTrainedModelWeight: (modelId: string, payload: { deployment_name: string }) =>
    request<TrainedModelRecord>(`/trained-models/${modelId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  listDatasets: (params: { task?: string; status?: string } = {}) =>
    request<ListResponse<DatasetRecord>>(`/datasets${query(params)}`),
  createDataset: (payload: { name: string; task: string; class_schema: Record<string, unknown>; source?: string }) =>
    request<DatasetRecord>("/datasets", { method: "POST", body: JSON.stringify(payload) }),
  deleteDataset: (id: string) => request<void>(`/datasets/${id}`, { method: "DELETE" }),
  listDatasetSamples: (datasetId: string, params: { split?: string; limit?: number; offset?: number } = {}) =>
    request<ListResponse<DatasetSampleRecord>>(`/datasets/${datasetId}/samples${query(params)}`),
  assignDatasetSplitRatio: (
    datasetId: string,
    payload: { train_ratio: number; val_ratio: number; test_ratio: number },
  ) =>
    request<ListResponse<DatasetSampleRecord>>(`/datasets/${datasetId}/samples/splits:ratio`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  datasetSampleContentUrl: (datasetId: string, sampleId: string) =>
    `${API_BASE_URL}/datasets/${datasetId}/samples/${sampleId}/content`,
  datasetExportUrl: (datasetId: string) => `${API_BASE_URL}/datasets/${datasetId}/export`,
  uploadDatasetSample: (datasetId: string, file: File) => {
    const formData = new FormData();
    formData.set("file", file, file.name);
    return request<SampleUploadResponse>(`/datasets/${datasetId}/samples:upload`, {
      method: "POST",
      body: formData
    });
  },
  uploadDatasetBatch: (datasetId: string, files: File[]) => {
    const formData = new FormData();
    files.forEach((file) => {
      formData.append("files", file, file.name);
      formData.append("relative_paths", file.webkitRelativePath || file.name);
    });
    return request<SampleUploadResponse>(`/datasets/${datasetId}/samples:upload-batch`, {
      method: "POST",
      body: formData
    });
  },
  analyzeDataset: (id: string) => request<TaskRecord>(`/datasets/${id}/analyze`, { method: "POST" }),
  processDataset: (
    id: string,
    payload: { augment?: Record<string, boolean>; clean?: Record<string, boolean>; max_samples?: number },
  ) => request<TaskRecord>(`/datasets/${id}/process`, { method: "POST", body: JSON.stringify(payload) }),
  validateDataset: (id: string) => request<TaskRecord>(`/datasets/${id}/validate`, { method: "POST" }),
  listLabelProjects: (datasetId: string) =>
    request<{ items: LabelProjectRecord[]; total: number }>(`/datasets/${datasetId}/label-projects`),
  createLabelProject: (datasetId: string, payload: { external_project_id?: string } = {}) =>
    request<LabelProjectRecord>(`/datasets/${datasetId}/label-projects`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  syncLabelProjectSamples: (projectId: string) =>
    request<TaskRecord>(`/label-projects/${projectId}/sync-samples`, { method: "POST" }),
  importLabelProjectAnnotations: (projectId: string) =>
    request<TaskRecord>(`/label-projects/${projectId}/import-annotations`, { method: "POST" }),
  createPipeline: (payload: Record<string, unknown>) =>
    request<TrainingPipelineRecord>("/pipelines", { method: "POST", body: JSON.stringify(payload) }),
  listPipelines: () => request<ListResponse<TrainingPipelineRecord>>("/pipelines"),
  updatePipeline: (pipelineId: string, payload: Record<string, unknown>) =>
    request<TrainingPipelineRecord>(`/pipelines/${pipelineId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deletePipeline: (pipelineId: string) => request<void>(`/pipelines/${pipelineId}`, { method: "DELETE" }),
  createTrainingJob: (pipelineId: string, payload: Record<string, unknown>) =>
    request<TrainingJobRecord>(`/pipelines/${pipelineId}/jobs`, { method: "POST", body: JSON.stringify(payload) }),
  listTrainingJobs: (params: { pipeline_id?: string; status?: string; limit?: number; offset?: number } = {}) =>
    request<ListResponse<TrainingJobRecord>>(`/training-jobs${query(params)}`),
  getTrainingObservabilitySummary: (trainingJobId: string) =>
    request<TrainingObservabilitySummary>(`/training-jobs/${trainingJobId}/observability/summary`),
  getTrainingObservabilityScalars: (trainingJobId: string, params: TrainingObservabilityScalarsParams) =>
    request<TrainingObservabilityScalars>(
      `/training-jobs/${trainingJobId}/observability/scalars${query({
        keys: params.keys.join(","),
        start_step: params.start_step,
        end_step: params.end_step,
        max_points: params.max_points
      })}`
    ),
  getTrainingObservabilityResources: (
    trainingJobId: string,
    params: TrainingObservabilityRangeParams = {},
  ) =>
    request<TrainingObservabilityResources>(
      `/training-jobs/${trainingJobId}/observability/resources${query(params)}`
    ),
  getTrainingObservabilityGraph: (trainingJobId: string) =>
    request<TrainingObservabilityGraph>(`/training-jobs/${trainingJobId}/observability/graph`),
  getTrainingObservabilityHistogram: (trainingJobId: string, params: TrainingObservabilityHistogramParams) =>
    request<TrainingObservabilityHistogram>(
      `/training-jobs/${trainingJobId}/observability/histograms${query(params)}`
    ),
  trainingJobLogUrl: (trainingJobId: string) => `${API_BASE_URL}/training-jobs/${trainingJobId}/log`,
  trainingJobVisualizationUrl: (trainingJobId: string, name: string) =>
    `${API_BASE_URL}/training-jobs/${trainingJobId}/visualizations/${encodeURIComponent(name)}`,
  listTrainingJobArtifacts: (trainingJobId: string) =>
    request<{ items: TrainingArtifactRecord[] }>(`/training-jobs/${trainingJobId}/artifacts`),
  trainingJobArtifactDownloadUrl: (trainingJobId: string, kind: TrainingArtifactRecord["kind"], name: string) =>
    `${API_BASE_URL}/training-jobs/${trainingJobId}/artifacts/${kind}/${encodeURIComponent(name)}`,
  predictPipelineImage: (pipelineId: string, payload: { file: File; model_weight: string; environment: string }) => {
    const formData = new FormData();
    formData.set("file", payload.file, payload.file.name);
    formData.set("model_weight", payload.model_weight);
    formData.set("environment", payload.environment);
    return request<PipelinePredictResponse>(`/pipelines/${pipelineId}/predict/image`, {
      method: "POST",
      body: formData
    });
  },
  evaluatePipeline: (
    pipelineId: string,
    payload: { evaluation_set: "val" | "custom"; dataset_id?: string; model_weight: string; environment: string },
  ) =>
    request<PipelineEvaluationResponse>(`/pipelines/${pipelineId}/evaluate`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listPipelineEvaluations: (pipelineId: string, params: { limit?: number; offset?: number } = {}) =>
    request<ListResponse<PipelineEvaluationResponse>>(`/pipelines/${pipelineId}/evaluations${query(params)}`),
  listResourcePools: () => request<{ items: ResourcePoolRecord[]; total: number }>("/resource-pools"),
  listNodes: () => request<{ items: ComputeNodeRecord[]; total: number }>("/nodes"),
  createService: (payload: ServiceCreatePayload) =>
    request<DeploymentServiceRecord>("/services", { method: "POST", body: JSON.stringify(payload) }),
  listServices: (params: { status?: string; pipeline_id?: string; limit?: number; offset?: number } = {}) =>
    request<ListResponse<DeploymentServiceRecord>>(`/services${query(params)}`),
  getService: (serviceId: string) => request<DeploymentServiceRecord>(`/services/${serviceId}`),
  stopService: (serviceId: string) =>
    request<DeploymentServiceRecord>(`/services/${serviceId}/stop`, { method: "POST" }),
  rollbackService: (serviceId: string) =>
    request<DeploymentServiceRecord>(`/services/${serviceId}/rollback`, { method: "POST" }),
  readServiceLog: (logUri: string) => requestText(logUri),
  updateService: (serviceId: string, payload: { status: "running" | "stopped" }) =>
    request<DeploymentServiceRecord>(`/services/${serviceId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteService: (serviceId: string) => request<void>(`/services/${serviceId}`, { method: "DELETE" }),
  predictServiceImage: (serviceId: string, file: File) => {
    const formData = new FormData();
    formData.set("file", file, file.name);
    return request<PipelinePredictResponse>(`/services/${serviceId}/predict/image`, { method: "POST", body: formData });
  },
  listTasks: (params: { limit?: number; offset?: number } = {}) =>
    request<ListResponse<TaskRecord>>(`/tasks${query(params)}`),
  cancelTask: (id: string) => request<TaskRecord>(`/tasks/${id}/cancel`, { method: "POST" })
};
