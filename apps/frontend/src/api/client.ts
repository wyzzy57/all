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
  listTasks: (params: { limit?: number; offset?: number } = {}) =>
    request<ListResponse<TaskRecord>>(`/tasks${query(params)}`),
  cancelTask: (id: string) => request<TaskRecord>(`/tasks/${id}/cancel`, { method: "POST" })
};
