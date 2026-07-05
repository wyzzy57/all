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
  sample_count: number;
  annotation_count: number;
};

export type SampleUploadResponse = {
  created_count: number;
  duplicate_count: number;
  skipped_count: number;
  samples: Array<{
    id: string;
    dataset_id: string;
    file_uri: string;
    width?: number | null;
    height?: number | null;
    checksum?: string | null;
    split?: string | null;
    annotation_status: string;
  }>;
};

export type LabelProjectRecord = {
  id: string;
  dataset_id: string;
  provider: string;
  external_project_id?: string | null;
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
};

export type TrainingJobRecord = {
  id: string;
  pipeline_id: string;
  status: string;
  task_id?: string | null;
  trained_model_id?: string | null;
  metrics: Record<string, unknown>;
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
  created_at: string;
  updated_at: string;
};

export type DeviceRecord = {
  id: string;
  name: string;
  endpoint_url: string;
  status: string;
  resource_info: Record<string, unknown>;
};

export type CameraRecord = {
  id: string;
  device_id: string;
  name: string;
  rtsp_url: string;
  status: string;
};

export type EdgeAppRecord = {
  id: string;
  name: string;
  description?: string | null;
  status: string;
};

export type EdgeAppVersionRecord = {
  id: string;
  edge_app_id: string;
  trained_model_id?: string | null;
  version: string;
  package_uri: string;
  status: string;
  checksum?: string | null;
};

export type DeploymentRecord = {
  id: string;
  device_id: string;
  edge_app_version_id: string;
  task_id?: string | null;
  status: string;
  active: boolean;
  logs_uri?: string | null;
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
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
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
  downloadBaseModel: (id: string) => request<BaseModelRecord>(`/base-models/${id}/download`, { method: "POST" }),
  listTrainedModels: () => request<ListResponse<Record<string, unknown>>>("/trained-models"),
  listDatasets: (params: { task?: string; status?: string } = {}) =>
    request<ListResponse<DatasetRecord>>(`/datasets${query(params)}`),
  createDataset: (payload: { name: string; task: string; class_schema: Record<string, unknown>; source?: string }) =>
    request<DatasetRecord>("/datasets", { method: "POST", body: JSON.stringify(payload) }),
  uploadDatasetSample: (datasetId: string, file: File) => {
    const formData = new FormData();
    formData.set("file", file, file.name);
    return request<SampleUploadResponse>(`/datasets/${datasetId}/samples:upload`, {
      method: "POST",
      body: formData
    });
  },
  analyzeDataset: (id: string) => request<unknown>(`/datasets/${id}/analyze`, { method: "POST" }),
  validateDataset: (id: string) => request<unknown>(`/datasets/${id}/validate`, { method: "POST" }),
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
  createTrainingJob: (pipelineId: string, payload: Record<string, unknown>) =>
    request<TrainingJobRecord>(`/pipelines/${pipelineId}/jobs`, { method: "POST", body: JSON.stringify(payload) }),
  listTrainingJobs: (params: { pipeline_id?: string; status?: string } = {}) =>
    request<ListResponse<TrainingJobRecord>>(`/training-jobs${query(params)}`),
  listTasks: (params: { limit?: number; offset?: number } = {}) =>
    request<ListResponse<TaskRecord>>(`/tasks${query(params)}`),
  cancelTask: (id: string) => request<TaskRecord>(`/tasks/${id}/cancel`, { method: "POST" }),
  listDevices: () => request<ListResponse<DeviceRecord>>("/devices"),
  createDevice: (payload: Record<string, unknown>) =>
    request<DeviceRecord>("/devices", { method: "POST", body: JSON.stringify(payload) }),
  checkDevice: (id: string) => request<unknown>(`/devices/${id}:check`, { method: "POST" }),
  listCameras: (deviceId: string) => request<ListResponse<CameraRecord>>(`/devices/${deviceId}/cameras`),
  createCamera: (deviceId: string, payload: Record<string, unknown>) =>
    request<CameraRecord>(`/devices/${deviceId}/cameras`, { method: "POST", body: JSON.stringify(payload) }),
  testCamera: (id: string) => request<unknown>(`/cameras/${id}:test`, { method: "POST" }),
  listEdgeApps: () => request<ListResponse<EdgeAppRecord>>("/edge-apps"),
  createEdgeApp: (payload: Record<string, unknown>) =>
    request<EdgeAppRecord>("/edge-apps", { method: "POST", body: JSON.stringify(payload) }),
  createEdgeAppVersion: (edgeAppId: string, payload: Record<string, unknown>) =>
    request<EdgeAppVersionRecord>(`/edge-apps/${edgeAppId}/versions`, { method: "POST", body: JSON.stringify(payload) }),
  listEdgeAppVersions: (edgeAppId: string) =>
    request<ListResponse<EdgeAppVersionRecord>>(`/edge-apps/${edgeAppId}/versions`),
  listDeployments: () => request<ListResponse<DeploymentRecord>>("/deployments"),
  createDeployment: (payload: Record<string, unknown>) =>
    request<DeploymentRecord>("/deployments", { method: "POST", body: JSON.stringify(payload) }),
  stopDeployment: (id: string) => request<DeploymentRecord>(`/deployments/${id}:stop`, { method: "POST" }),
  rollbackDeployment: (id: string, target_edge_app_version_id: string) =>
    request<DeploymentRecord>(`/deployments/${id}:rollback`, {
      method: "POST",
      body: JSON.stringify({ target_edge_app_version_id })
    })
};
