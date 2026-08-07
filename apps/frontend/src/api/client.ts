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
  source_path?: string;
  status: string;
  local_uri?: string | null;
  checksum?: string | null;
  size_bytes?: number | null;
};

export type DatasetRecord = {
  id: string;
  name: string;
  task: string;
  status: string;
  format?: "yolo" | "alpaca" | "sharegpt" | "openai_messages" | string;
  class_schema?: { names?: unknown[]; format?: string };
  schema_config?: Record<string, unknown>;
  manifest_checksum?: string | null;
  sample_count: number;
  annotation_count: number;
  source?: string;
  created_at?: string;
  updated_at?: string;
  visibility?: "private" | "shared" | "organization" | string;
  owner_user_id?: string | null;
  asset_role?: "working" | "published" | string;
  storage_uri?: string | null;
};

export type DatasetVersionRecord = {
  id: string;
  dataset_id: string;
  version: number;
  status: string;
  format: string;
  object_uri: string;
  manifest_uri: string;
  manifest_checksum: string;
  source_revision?: string | null;
  total_count: number;
  valid_count: number;
  invalid_count: number;
  skipped_count: number;
  size_bytes: number;
  published_at: string;
};

export type DatasetConversionResult = {
  version: DatasetVersionRecord;
  reused: boolean;
  total_count: number;
  valid_count: number;
  invalid_count: number;
  skipped_count: number;
  issues: Array<{ sample_id?: string; code: string; message: string }>;
};

export type PagedResponse<T> = {
  items: T[];
  total: number;
};

export type AuthenticatedUser = {
  id: string;
  username: string;
  display_name: string;
  email: string;
  role: "admin" | "member";
  status: string;
  must_change_password: boolean;
};

export type LoginResponse = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: AuthenticatedUser;
};

export type UserRecord = AuthenticatedUser & {
  temporary_password?: string;
};

export type UserGroupRecord = {
  id: string;
  name: string;
  description: string | null;
  status: string;
  member_ids: string[];
  member_count: number;
};

export type ResourceGrantRecord = {
  id: string;
  resource_type: string;
  resource_id: string;
  principal_type: "user" | "group" | "organization";
  principal_id: string;
  permissions: string[];
  expires_at: string | null;
};

export type ResourceSharingRecord = {
  resource_type: string;
  resource_id: string;
  visibility: "private" | "shared" | "organization" | string;
  grants: ResourceGrantRecord[];
};

export type SharingPrincipalList = {
  organization: { id: string; name: string };
  users: Array<{ id: string; username: string; display_name: string }>;
  groups: Array<{ id: string; name: string }>;
};

export type ResourceSharingGrantInput = {
  principal_type: "user" | "group" | "organization";
  principal_id: string;
  permissions: string[];
  expires_at?: string | null;
};

export type ResourceAllocationRecord = {
  id: string;
  principal_type: "user" | "group";
  principal_id: string;
  resource_pool_id: string;
  max_concurrent_training_jobs: number | null;
  max_gpu_count: number | null;
  max_service_instances: number | null;
  expires_at: string | null;
};

export type AuditLogRecord = {
  id: string;
  actor_user_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  result: string;
  request_id: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
};

export type UserListResponse = PagedResponse<UserRecord>;
export type UserGroupListResponse = PagedResponse<UserGroupRecord>;
export type ResourceGrantListResponse = PagedResponse<ResourceGrantRecord>;
export type ResourceAllocationListResponse = PagedResponse<ResourceAllocationRecord>;
export type AuditLogListResponse = PagedResponse<AuditLogRecord> & {
  next_cursor: string | null;
};

export type StatisticsBucket = {
  label: string;
  value: number;
};

export type StatisticsTrend = {
  labels: string[];
  values: number[];
};

export type WorkbenchStatistics = {
  generated_at: string;
  totals: {
    pipelines: number;
    datasets: number;
    training_jobs: number;
    services: number;
    nodes: number;
    users: number;
    groups: number;
  };
  status_buckets: Record<string, StatisticsBucket[]>;
  creation_trends: Record<string, StatisticsTrend>;
};

export type RecentFailure = {
  resource_type: "pipeline" | "training_job" | "service" | "node";
  resource_id: string;
  name: string;
  status: string;
  updated_at: string;
};

export type AdminStatistics = WorkbenchStatistics & {
  recent_failures: RecentFailure[];
};

export type ResourceMetric = {
  value: number | null;
  available: number;
  unavailable: number;
};

export type GpuMetric = {
  value: number | null;
  available: boolean;
};

export type ResourceStatistics = {
  generated_at: string;
  staleness_threshold_seconds: number;
  nodes: {
    status_buckets: StatisticsBucket[];
    freshness: {
      fresh: number;
      stale: number;
      unknown: number;
      oldest_fresh_at: string | null;
      newest_fresh_at: string | null;
    };
    resource_usage: Record<string, ResourceMetric>;
  };
  gpus: {
    series: Array<{
      key: string;
      refreshed_at: string;
      utilization_percent: GpuMetric;
      memory_used_mib: GpuMetric;
      memory_total_mib: GpuMetric;
      memory_utilization_percent: GpuMetric;
    }>;
  };
  services: {
    calls: number;
    instances: number;
    health_buckets: StatisticsBucket[];
    latest_health_checked_at: string | null;
  };
  group_allocation_usage: {
    policy_count: number;
    resource_pool_count: number;
    active_training_runs: number;
    active_service_instances: number;
    active_workloads: number;
    limitation: string;
  };
};

export type LlmDatasetMessage = {
  role: string;
  content: string;
};

export type LlmDatasetPreview = {
  dataset_id: string;
  format: string;
  manifest_checksum: string;
  samples: Array<{
    index: number;
    messages: LlmDatasetMessage[];
    character_count: number;
    token_estimate: number;
  }>;
  token_analysis: {
    method: string;
    exact: boolean;
    sample_count: number;
    minimum: number;
    maximum: number;
    average: number;
  };
};

export type LlmDatasetValidation = {
  dataset: DatasetRecord;
  total_count: number;
  valid_count: number;
  invalid_count: number;
  issues: Array<{ index: number; code: string; message: string }>;
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
  engine?: "yolo26" | "ultralytics" | "paddlex" | "llamafactory";
  task: string;
  scale: string;
  status: string;
  task_kind?: string;
  framework?: string;
  adapter_key?: string;
  adapter_version?: string;
  model_family?: string;
  recipe?: Record<string, unknown>;
  framework_locked_at?: string | null;
  first_submitted_job_id?: string | null;
  cloned_from_pipeline_id?: string | null;
  base_model_id?: string | null;
  dataset_id?: string | null;
  params_template?: Record<string, unknown>;
  default_environment?: Record<string, unknown>;
  is_public?: boolean;
  public_scope?: Record<string, unknown>;
  visibility?: "private" | "shared" | "organization" | string;
  owner_user_id?: string | null;
  is_favorite?: boolean;
  created_at?: string;
  updated_at?: string;
};

export type AdapterIdentityRecord = {
  framework: string;
  adapter_key: string;
  adapter_version: string;
};

export type FrameworkModelCapabilityRecord = {
  model_key: string;
  display_name: string;
  runtime_id?: string | null;
  family?: string | null;
  variant?: string | null;
  source?: string | null;
  revision?: string | null;
  sources?: string[];
};

export type FrameworkParameterCapabilityRecord = {
  name: string;
  value_type: "string" | "integer" | "number" | "boolean";
  required: boolean;
  default?: string | number | boolean | null;
  minimum?: number | null;
  maximum?: number | null;
  choices?: Array<string | number | boolean>;
  description?: string | null;
  help_text?: string | null;
  advanced_group?: string | null;
};

export type FrameworkResourceCapabilityRecord = {
  resource_kinds: Array<"cpu" | "cuda">;
  cpu_cores_min: number;
  memory_mb_min: number;
  gpu_count_min: number;
  gpu_memory_mb_min: number;
};

export type FrameworkTaskCapabilityRecord = {
  task_type: string;
  models: FrameworkModelCapabilityRecord[];
  accepted_dataset_formats: string[];
  convertible_dataset_formats: string[];
  resources: FrameworkResourceCapabilityRecord;
  parameters: FrameworkParameterCapabilityRecord[];
  operations?: Array<{
    name: "train" | "stop" | "resume" | "evaluate" | "image_inference" | "export" | "deploy";
    supported: boolean;
    implemented: boolean;
    available: boolean;
    unavailable_reason?: string | null;
  }>;
  observable_metrics?: string[];
  observable_artifacts?: string[];
};

export type FrameworkCapabilityRecord = AdapterIdentityRecord & {
  display_name?: string | null;
  framework_version?: string | null;
  framework_version_constraint?: string | null;
  base_image_reference?: string | null;
  runtime_components?: Array<{ key: string; value: string }>;
  training_runtime_image_digest?: string | null;
  inference_runtime_image_digest?: string | null;
  availability_baseline_operation?: string;
  available: boolean;
  unavailable_reason?: string | null;
  tasks: FrameworkTaskCapabilityRecord[];
};

export type FrameworkCapabilityCatalogResponse = {
  task_kind: string | null;
  adapters: FrameworkCapabilityRecord[];
};

export type TrainingAttemptRecord = {
  id: string;
  training_job_id: string;
  attempt_number: number;
  resolved_snapshot: Record<string, unknown>;
  artifact_manifest: Record<string, unknown>;
};

export type TrainingResolvedSnapshot = {
  framework?: string;
  adapter_key?: string;
  adapter_version?: string;
  runtime_image_digest?: string;
  model?: { id?: string; family?: string; runtime_id?: string };
  dataset?: { id?: string; version_id?: string; version?: number; format?: string };
};

export type ArtifactManifestRecord = {
  adapter_identity: AdapterIdentityRecord;
  role_artifacts: Array<Record<string, unknown>>;
};

export type LlmModelResolution = {
  source: "huggingface" | "modelscope";
  model_id: string;
  requested_revision: string;
  resolved_revision: string;
  immutable_revision: boolean;
  pipeline_tag?: string | null;
  library_name?: string | null;
  license?: string | null;
  gated: boolean;
  private: boolean;
  size_bytes?: number | null;
};

export type TrainingJobRecord = {
  id: string;
  pipeline_id: string;
  status: string;
  task_id?: string | null;
  distributed_run_id?: string | null;
  remote_execution_id?: string | null;
  trained_model_id?: string | null;
  environment?: Record<string, unknown>;
  params?: Record<string, unknown>;
  resolved_snapshot?: TrainingResolvedSnapshot;
  attempts?: Array<Pick<TrainingAttemptRecord, "id" | "attempt_number">>;
  metrics: Record<string, unknown>;
  log_uri?: string | null;
  log_stream_id?: string | null;
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
  canonical_name?: string;
  raw_name?: string;
  unit?: string;
  split?: string | null;
  step: number;
  epoch?: number | null;
  value: number;
  timestamp: number;
  source?: string;
};

export type TrainingObservabilityScalars = {
  series: Record<string, TrainingObservabilityScalarPoint[]>;
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilityResources = {
  series: Record<string, TrainingObservabilityScalarPoint[]>;
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilitySecondaryAction = {
  source: string;
  url: string;
};

export type TrainingObservabilitySummary = {
  job_id: string;
  engine: "yolo26" | "ultralytics" | "paddlex" | "llamafactory";
  pipeline_id: string;
  pipeline_name: string;
  status: string;
  progress: Record<string, unknown>;
  timing: Record<string, unknown>;
  environment: Record<string, unknown>;
  latest_metrics: Record<string, number>;
  available_scalar_keys: string[];
  secondary_actions: TrainingObservabilitySecondaryAction[];
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilityFinding = {
  code: string;
  severity: "info" | "warning" | "critical" | string;
  title: string;
  message: string;
  metric_names: string[];
  step_range: number[] | null;
  observed_values: Record<string, number | null>;
};

export type TrainingObservabilityAnalysis = {
  findings: TrainingObservabilityFinding[];
  availability: TrainingObservabilityAvailability;
};

export type TrainingObservabilityArtifact = {
  path: string;
  size_bytes: number;
  sha256: string;
  download_url: string;
};

export type TrainingObservabilityArtifacts = {
  items: TrainingObservabilityArtifact[];
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
  desired_state?: "running" | "stopped" | string;
  active_revision?: number | null;
  endpoint: string;
  calls: number;
  config: Record<string, unknown>;
  instance_id?: string | null;
  deployment_revision?: number | null;
  node_id?: string | null;
  container_id?: string | null;
  image_digest?: string | null;
  model_checksum?: string | null;
  engine?: string | null;
  engine_digest?: string | null;
  port?: number | null;
  health_status?: string | null;
  health_checked_at?: string | null;
  task_id?: string | null;
  remote_execution_id?: string | null;
  phase?: string | null;
  log_uri?: string | null;
  log_stream_id?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  visibility?: "private" | "shared" | "organization" | string;
  owner_user_id?: string | null;
};

export type LogStreamRecord = {
  id: string;
  resource_type: string;
  resource_id: string;
  source: string;
  status: string;
  total_bytes: number;
  line_count: number;
  redacted_log_uri?: string | null;
  created_at: string;
  updated_at: string;
};

export type LogLineRecord = {
  timestamp?: string | null;
  source?: string | null;
  level?: string | null;
  message: unknown;
};

export type LogChunkPage = {
  lines: LogLineRecord[];
  next_cursor: string | null;
  has_more: boolean;
  bytes_read: number;
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
  enabled: boolean;
  labels: Record<string, string>;
  connection_method: "ssh" | "agent" | string;
  inventory_refreshed_at?: string | null;
  resource_revision: number;
  certificate_expires_at?: string | null;
  last_seen_at?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type SshHostKeyRecord = {
  status: string;
  host_key_type: string;
  fingerprint: string;
};

export type ManualNodePayload = {
  name: string;
  host: string;
  port: number;
  administrator: string;
  password: string;
  confirmed_fingerprint: string;
  labels: Record<string, string>;
  resource_pool_id?: string;
};

export type NodeProbeRecord = {
  status: string;
  node_id: string;
  supported: boolean;
  unsupported_reasons: string[];
  compatibility_key: string;
  resource_pool_id?: string | null;
  inventory: Record<string, unknown>;
};

export type ServiceCreatePayload = {
  name: string;
  pipeline_id: string;
  trained_model_id?: string;
  base_model_id?: string;
  model_name: string;
  model_weight: string;
  environment: string;
  instance_name: string;
  resource_summary: string;
  node_id: string;
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

let accessToken: string | null = null;
let authEpoch = 0;
let tokenGeneration = 0;
let refreshFlight: {
  epoch: number;
  tokenGeneration: number;
  operations: Set<AuthOperation>;
  promise: Promise<LoginResponse>;
} | null = null;
export type AuthOperation = symbol;
export type AuthSessionEvent = { operations: ReadonlySet<AuthOperation> };
type AuthSessionSubscriber = (session: LoginResponse | null, event: AuthSessionEvent) => void;
const sessionSubscribers = new Set<AuthSessionSubscriber>();
const NO_AUTH_OPERATIONS: ReadonlySet<AuthOperation> = new Set();

export function setAccessToken(token: string): void {
  authEpoch += 1;
  tokenGeneration += 1;
  accessToken = token;
}

export function clearAccessToken(): void {
  invalidateSession(true);
}

export function subscribeAuthSession(subscriber: AuthSessionSubscriber): () => void {
  sessionSubscribers.add(subscriber);
  return () => sessionSubscribers.delete(subscriber);
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
  replayed = false,
  operation?: AuthOperation,
): Promise<T> {
  const requestEpoch = authEpoch;
  const requestTokenGeneration = tokenGeneration;
  const requestToken = accessToken;
  const isFormData = init?.body instanceof FormData;
  const headers = buildHeaders(init?.headers, isFormData, requestToken);
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    const error = await responseError(response);
    if (response.status === 401 && !replayed && !isAuthRequest(path)) {
      if (requestEpoch !== authEpoch) throw new StaleAuthSessionError();
      if (requestTokenGeneration !== tokenGeneration) {
        if (accessToken) return request<T>(path, init, true, operation);
        throw new StaleAuthSessionError();
      }
      try {
        await sharedRefresh(requestEpoch, requestTokenGeneration, operation);
      } catch (refreshError) {
        throw refreshError instanceof Error ? refreshError : error;
      }
      return request<T>(path, init, true, operation);
    }
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function buildHeaders(
  initial: HeadersInit | undefined,
  isFormData: boolean,
  token: string | null,
): Record<string, string> | undefined {
  const headers: Record<string, string> = {};
  if (initial instanceof Headers) {
    initial.forEach((value, key) => {
      headers[key] = value;
    });
  } else if (Array.isArray(initial)) {
    initial.forEach(([key, value]) => {
      headers[key] = value;
    });
  } else if (initial) {
    Object.entries(initial).forEach(([key, value]) => {
      headers[key] = String(value);
    });
  }
  if (!isFormData && !hasHeader(headers, "content-type")) headers["Content-Type"] = "application/json";
  if (token && !hasHeader(headers, "authorization")) headers.Authorization = `Bearer ${token}`;
  return Object.keys(headers).length ? headers : undefined;
}

function hasHeader(headers: Record<string, string>, name: string): boolean {
  return Object.keys(headers).some((key) => key.toLowerCase() === name);
}

function isAuthRequest(path: string): boolean {
  return path === "/auth/login" || path === "/auth/refresh" || path === "/auth/logout";
}

async function responseError(response: Response): Promise<ApiError> {
  const detail = await response.text();
  return new ApiError(readErrorDetail(detail) || `HTTP ${response.status}`, response.status);
}

class StaleAuthSessionError extends Error {
  constructor() {
    super("Authentication session changed while the request was in flight");
    this.name = "StaleAuthSessionError";
  }
}

function publishSession(
  session: LoginResponse | null,
  operations: ReadonlySet<AuthOperation> = NO_AUTH_OPERATIONS,
): void {
  const event = { operations };
  sessionSubscribers.forEach((subscriber) => subscriber(session, event));
}

function invalidateSession(
  publish: boolean,
  operations: ReadonlySet<AuthOperation> = NO_AUTH_OPERATIONS,
): void {
  authEpoch += 1;
  tokenGeneration += 1;
  accessToken = null;
  if (publish) publishSession(null, operations);
}

function installSession(
  response: LoginResponse,
  expectedEpoch: number,
  expectedTokenGeneration: number,
  operations: ReadonlySet<AuthOperation> = NO_AUTH_OPERATIONS,
): LoginResponse {
  if (authEpoch !== expectedEpoch || tokenGeneration !== expectedTokenGeneration) {
    throw new StaleAuthSessionError();
  }
  accessToken = response.access_token;
  tokenGeneration += 1;
  publishSession(response, operations);
  return response;
}

function failSession(
  expectedEpoch: number,
  expectedTokenGeneration: number,
  operations: ReadonlySet<AuthOperation>,
): void {
  if (authEpoch !== expectedEpoch || tokenGeneration !== expectedTokenGeneration) return;
  invalidateSession(true, operations);
}

function sharedRefresh(
  expectedEpoch = authEpoch,
  expectedTokenGeneration = tokenGeneration,
  operation?: AuthOperation,
): Promise<LoginResponse> {
  if (
    refreshFlight
    && refreshFlight.epoch === expectedEpoch
    && refreshFlight.tokenGeneration === expectedTokenGeneration
  ) {
    if (operation) refreshFlight.operations.add(operation);
    return refreshFlight.promise;
  }

  const operations = new Set<AuthOperation>();
  if (operation) operations.add(operation);
  const flight = {
    epoch: expectedEpoch,
    tokenGeneration: expectedTokenGeneration,
    operations,
    promise: Promise.resolve(undefined as unknown as LoginResponse),
  };
  flight.promise = request<LoginResponse>("/auth/refresh", { method: "POST" })
      .then((response) => {
        return installSession(response, expectedEpoch, expectedTokenGeneration, operations);
      })
      .catch((error) => {
        if (!(error instanceof StaleAuthSessionError)) {
          failSession(expectedEpoch, expectedTokenGeneration, operations);
        }
        throw error;
      })
      .finally(() => {
        if (refreshFlight === flight) refreshFlight = null;
      });
  refreshFlight = flight;
  return flight.promise;
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
  login: async (payload: { username: string; password: string }, operation?: AuthOperation) => {
    const operations = operation ? new Set([operation]) : NO_AUTH_OPERATIONS;
    invalidateSession(true, operations);
    const expectedEpoch = authEpoch;
    const expectedTokenGeneration = tokenGeneration;
    const response = await request<LoginResponse>("/auth/login", { method: "POST", body: JSON.stringify(payload) });
    return installSession(response, expectedEpoch, expectedTokenGeneration, operations);
  },
  refresh: (operation?: AuthOperation) => sharedRefresh(authEpoch, tokenGeneration, operation),
  logout: async (operation?: AuthOperation) => {
    invalidateSession(true, operation ? new Set([operation]) : NO_AUTH_OPERATIONS);
    await request<void>("/auth/logout", { method: "POST" });
  },
  me: () => request<AuthenticatedUser>("/auth/me"),
  updateProfile: (payload: { display_name?: string; email?: string }, operation?: AuthOperation) =>
    request<AuthenticatedUser>(
      "/account/profile",
      { method: "PATCH", body: JSON.stringify(payload) },
      false,
      operation,
    ),
  changePassword: async (
    payload: { current_password: string; new_password: string },
    operation?: AuthOperation,
  ) => {
    authEpoch += 1;
    const passwordChangeEpoch = authEpoch;
    await request<void>(
      "/account/change-password",
      { method: "POST", body: JSON.stringify(payload) },
      false,
      operation,
    );
    if (authEpoch === passwordChangeEpoch) {
      invalidateSession(true, operation ? new Set([operation]) : NO_AUTH_OPERATIONS);
    }
  },
  listUsers: (params: { search?: string; status?: string; role?: string; limit?: number; offset?: number } = {}) =>
    request<UserListResponse>(`/admin/users${query(params)}`),
  createUser: (payload: {
    username: string;
    display_name: string;
    email: string;
    role: "admin" | "member";
    temporary_password?: string;
  }) => request<UserRecord>("/admin/users", { method: "POST", body: JSON.stringify(payload) }),
  updateUser: (userId: string, payload: Partial<Pick<UserRecord, "display_name" | "email" | "role" | "status">>) =>
    request<UserRecord>(`/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  resetUserPassword: (userId: string, payload: { temporary_password?: string } = {}) =>
    request<{ user: UserRecord; temporary_password: string }>(`/admin/users/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteUser: (userId: string) => request<void>(`/admin/users/${userId}`, { method: "DELETE" }),
  listUserGroups: (params: { limit?: number; offset?: number } = {}) =>
    request<UserGroupListResponse>(`/admin/groups${query(params)}`),
  createUserGroup: (payload: { name: string; description?: string | null }) =>
    request<UserGroupRecord>("/admin/groups", { method: "POST", body: JSON.stringify(payload) }),
  updateUserGroup: (groupId: string, payload: { name?: string; description?: string | null }) =>
    request<UserGroupRecord>(`/admin/groups/${groupId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  replaceUserGroupMembers: (groupId: string, userIds: string[]) =>
    request<UserGroupRecord>(`/admin/groups/${groupId}/members`, {
      method: "PUT",
      body: JSON.stringify({ user_ids: userIds }),
    }),
  deleteUserGroup: (groupId: string) => request<void>(`/admin/groups/${groupId}`, { method: "DELETE" }),
  listResourceGrants: (params: { limit?: number; offset?: number } = {}) =>
    request<ResourceGrantListResponse>(`/admin/resource-grants${query(params)}`),
  upsertResourceGrant: (payload: Omit<ResourceGrantRecord, "id">) =>
    request<ResourceGrantRecord>("/admin/resource-grants", { method: "PUT", body: JSON.stringify(payload) }),
  deleteResourceGrant: (grantId: string) =>
    request<void>(`/admin/resource-grants/${grantId}`, { method: "DELETE" }),
  listResourceAllocations: (params: { limit?: number; offset?: number } = {}) =>
    request<ResourceAllocationListResponse>(`/admin/resource-allocations${query(params)}`),
  upsertResourceAllocation: (payload: Omit<ResourceAllocationRecord, "id">) =>
    request<ResourceAllocationRecord>("/admin/resource-allocations", { method: "PUT", body: JSON.stringify(payload) }),
  deleteResourceAllocation: (allocationId: string) =>
    request<void>(`/admin/resource-allocations/${allocationId}`, { method: "DELETE" }),
  listAuditLogs: (params: {
    actor_user_id?: string;
    resource_type?: string;
    resource_id?: string;
    action?: string;
    result?: string;
    created_from?: string;
    created_to?: string;
    cursor?: string;
    limit?: number;
  } = {}) =>
    request<AuditLogListResponse>(`/admin/audit-logs${query(params)}`),
  getWorkbenchStatistics: () => request<WorkbenchStatistics>("/statistics/workbench"),
  getResourceStatistics: () => request<ResourceStatistics>("/statistics/resources"),
  getAdminOverviewStatistics: () => request<AdminStatistics>("/admin/statistics/overview"),
  getAdminResourceStatistics: () => request<ResourceStatistics>("/admin/statistics/resources"),
  listSharingPrincipals: () => request<SharingPrincipalList>("/resources/sharing-principals"),
  getResourceSharing: (resourceType: string, resourceId: string) =>
    request<ResourceSharingRecord>(`/resources/${resourceType}/${resourceId}/sharing`),
  replaceResourceSharing: (
    resourceType: string,
    resourceId: string,
    payload: { grants: ResourceSharingGrantInput[] },
  ) => request<ResourceSharingRecord>(`/resources/${resourceType}/${resourceId}/sharing`, {
    method: "PUT",
    body: JSON.stringify(payload),
  }),
  resolveLlmModel: (payload: { source: "huggingface" | "modelscope"; model_id: string; revision: string }) =>
    request<LlmModelResolution>("/llm/models/resolve", { method: "POST", body: JSON.stringify(payload) }),
  listBaseModels: (params: { task?: string; status?: string } = {}) =>
    request<ListResponse<BaseModelRecord>>(`/base-models${query(params)}`),
  uploadBaseModel: (file: File, payload: { task: string; scale: string }) => {
    const formData = new FormData();
    formData.set("file", file, file.name);
    formData.set("task", payload.task);
    formData.set("scale", payload.scale);
    return request<BaseModelRecord>("/base-models:upload", { method: "POST", body: formData });
  },
  listTrainedModels: (params: { task?: string; pipeline_id?: string; status?: string; limit?: number; offset?: number } = {}) =>
    request<ListResponse<TrainedModelRecord>>(`/trained-models${query(params)}`),
  markTrainedModelWeight: (modelId: string, payload: { deployment_name: string }) =>
    request<TrainedModelRecord>(`/trained-models/${modelId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  listDatasets: (params: { task?: string; status?: string } = {}) =>
    request<ListResponse<DatasetRecord>>(`/datasets${query(params)}`),
  createDataset: (payload: { name: string; task: string; class_schema: Record<string, unknown>; source?: string; preparation?: boolean }) =>
    request<DatasetRecord>("/datasets", { method: "POST", body: JSON.stringify(payload) }),
  uploadLlmDataset: (payload: { name: string; file: File; format?: string }) => {
    const formData = new FormData();
    formData.set("name", payload.name);
    formData.set("format", payload.format || "auto");
    formData.set("file", payload.file, payload.file.name);
    return request<LlmDatasetValidation>("/datasets/llm/upload", { method: "POST", body: formData });
  },
  previewLlmDataset: (datasetId: string, limit = 5) =>
    request<LlmDatasetPreview>("/datasets/llm/preview", {
      method: "POST",
      body: JSON.stringify({ dataset_id: datasetId, limit }),
    }),
  promoteDataset: (id: string) => request<DatasetRecord>(`/datasets/${id}/promote`, { method: "POST" }),
  convertLabeledDataset: (id: string) =>
    request<DatasetConversionResult>(`/datasets/${id}/convert-labeled`, { method: "POST" }),
  listDatasetVersions: (id: string) => request<DatasetVersionRecord[]>(`/datasets/${id}/versions`),
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
  launchLabelProject: (projectId: string) =>
    request<{ launch_url: string; expires_in: number }>(`/label-projects/${projectId}/launch`, { method: "POST" }),
  getTask: (id: string) => request<TaskRecord>(`/tasks/${id}`),
  getFrameworkCapabilities: (taskKind?: string) =>
    request<FrameworkCapabilityCatalogResponse>(`/frameworks/capabilities${query({ task_kind: taskKind })}`),
  createPipeline: (payload: Record<string, unknown>) =>
    request<TrainingPipelineRecord>("/pipelines", { method: "POST", body: JSON.stringify(payload) }),
  listPipelines: () => request<ListResponse<TrainingPipelineRecord>>("/pipelines"),
  updatePipeline: (pipelineId: string, payload: Record<string, unknown>) =>
    request<TrainingPipelineRecord>(`/pipelines/${pipelineId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  clonePipeline: (pipelineId: string, payload: Record<string, unknown>) =>
    request<TrainingPipelineRecord>(`/pipelines/${pipelineId}/clone`, { method: "POST", body: JSON.stringify(payload) }),
  deletePipeline: (pipelineId: string) => request<void>(`/pipelines/${pipelineId}`, { method: "DELETE" }),
  createTrainingJob: (pipelineId: string, payload: Record<string, unknown>) =>
    request<TrainingJobRecord>(`/pipelines/${pipelineId}/jobs`, { method: "POST", body: JSON.stringify(payload) }),
  listTrainingJobs: (params: { pipeline_id?: string; status?: string; limit?: number; offset?: number } = {}) =>
    request<ListResponse<TrainingJobRecord>>(`/training-jobs${query(params)}`),
  deleteTrainingJob: (trainingJobId: string) =>
    request<void>(`/training-jobs/${trainingJobId}`, { method: "DELETE" }),
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
  getTrainingObservabilityAnalysis: (trainingJobId: string) =>
    request<TrainingObservabilityAnalysis>(`/training-jobs/${trainingJobId}/observability/analysis`),
  getTrainingObservabilityArtifacts: (trainingJobId: string) =>
    request<TrainingObservabilityArtifacts>(`/training-jobs/${trainingJobId}/observability/artifacts`),
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
  scanNodeHostKey: (payload: { host: string; port: number }) =>
    request<SshHostKeyRecord>("/edge-nodes/scan-host-key", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createManualNode: (payload: ManualNodePayload) =>
    request<ComputeNodeRecord>("/nodes/manual", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  refreshNode: (nodeId: string) =>
    request<NodeProbeRecord>(`/nodes/${nodeId}/refresh`, { method: "POST" }),
  enableNode: (nodeId: string) =>
    request<ComputeNodeRecord>(`/nodes/${nodeId}/enable`, { method: "POST" }),
  disableNode: (nodeId: string) =>
    request<ComputeNodeRecord>(`/nodes/${nodeId}/disable`, { method: "POST" }),
  deleteNode: (nodeId: string) => request<void>(`/nodes/${nodeId}`, { method: "DELETE" }),
  assignNodePool: (nodeId: string, resourcePoolId: string) =>
    request<ComputeNodeRecord>(`/nodes/${nodeId}/resource-pool`, {
      method: "PUT",
      body: JSON.stringify({ resource_pool_id: resourcePoolId }),
    }),
  createService: (payload: ServiceCreatePayload) =>
    request<DeploymentServiceRecord>("/services", { method: "POST", body: JSON.stringify(payload) }),
  listServices: (params: { status?: string; pipeline_id?: string; limit?: number; offset?: number } = {}) =>
    request<ListResponse<DeploymentServiceRecord>>(`/services${query(params)}`),
  getService: (serviceId: string) => request<DeploymentServiceRecord>(`/services/${serviceId}`),
  stopService: (serviceId: string) =>
    request<DeploymentServiceRecord>(`/services/${serviceId}/stop`, { method: "POST" }),
  startService: (serviceId: string) =>
    request<DeploymentServiceRecord>(`/services/${serviceId}/start`, { method: "POST" }),
  restartService: (serviceId: string) =>
    request<DeploymentServiceRecord>(`/services/${serviceId}/restart`, { method: "POST" }),
  rollbackService: (serviceId: string) =>
    request<DeploymentServiceRecord>(`/services/${serviceId}/rollback`, { method: "POST" }),
  readServiceLog: (logUri: string) => requestText(logUri),
  getLogStream: (streamId: string) => request<LogStreamRecord>(`/log-streams/${streamId}`),
  getLogChunks: (streamId: string, cursor?: string | null) =>
    request<LogChunkPage>(`/log-streams/${streamId}/chunks${query({ cursor: cursor || undefined })}`),
  downloadLogStream: async (streamId: string) => {
    const response = await fetch(`${API_BASE_URL}/log-streams/${streamId}/download`, {
      credentials: "include",
      headers: buildHeaders(undefined, true, accessToken),
    });
    if (!response.ok) throw await responseError(response);
    return response.blob();
  },
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
  cancelTask: (id: string) => request<TaskRecord>(`/tasks/${id}/cancel`, { method: "POST" }),
  deleteTask: (id: string) => request<void>(`/tasks/${id}`, { method: "DELETE" })
};
