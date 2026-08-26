import type {
  DeepAuditRequest,
  DeepAuditResponse,
  ErrorResponse,
  GenerationRequest,
  GenerationResponse,
  HealthResponse,
  ProjectCreateResponse,
  ProjectView,
} from "./types";


const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);


export interface RequestHandle {
  readonly signal: AbortSignal;
  cancel: () => void;
}


export class ApiClientError extends Error {
  readonly status: number;
  readonly errorCode: string;
  readonly retryable: boolean;
  readonly details: Record<string, unknown> | null;

  constructor(status: number, payload: ErrorResponse) {
    super(payload.message);
    this.name = "ApiClientError";
    this.status = status;
    this.errorCode = payload.error_code;
    this.retryable = payload.retryable;
    this.details = payload.details;
  }
}


function clientError(
  status: number,
  errorCode: string,
  message: string,
  retryable: boolean,
): ApiClientError {
  return new ApiClientError(status, {
    error_code: errorCode,
    message,
    retryable,
    details: null,
  });
}


function isErrorResponse(value: unknown): value is ErrorResponse {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<ErrorResponse>;
  return (
    typeof candidate.error_code === "string" &&
    typeof candidate.message === "string" &&
    typeof candidate.retryable === "boolean" &&
    (candidate.details === null ||
      candidate.details === undefined ||
      typeof candidate.details === "object")
  );
}


async function request(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE_URL}${path}`, init);
  } catch (error: unknown) {
    if (isRequestCancelled(error)) {
      throw error;
    }
    throw clientError(0, "NETWORK_ERROR", "无法连接本地服务，请检查服务后重试。", true);
  }
}


async function readError(response: Response): Promise<ApiClientError> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return clientError(
      response.status,
      "INVALID_RESPONSE",
      "服务返回了无法识别的错误响应，请重试。",
      response.status >= 500,
    );
  }
  if (!isErrorResponse(payload)) {
    return clientError(
      response.status,
      "INVALID_RESPONSE",
      "服务返回了不完整的错误信息，请重试。",
      response.status >= 500,
    );
  }
  return new ApiClientError(response.status, {
    ...payload,
    details: payload.details ?? null,
  });
}


async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init);

  if (!response.ok) {
    throw await readError(response);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw clientError(
      response.status,
      "INVALID_RESPONSE",
      "服务返回了无法读取的数据，请重试。",
      true,
    );
  }
}


function projectPath(projectId: string, suffix = ""): string {
  return `/api/projects/${encodeURIComponent(projectId)}${suffix}`;
}


export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/api/health", { signal });
}


export function createRequestHandle(): RequestHandle {
  const controller = new AbortController();
  return {
    signal: controller.signal,
    cancel: () => controller.abort(),
  };
}


export function isRequestCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}


export function createProject(
  file: File,
  signal?: AbortSignal,
): Promise<ProjectCreateResponse> {
  const body = new FormData();
  body.set("file", file);
  body.set("rights_confirmed", "true");
  return requestJson<ProjectCreateResponse>("/api/projects", {
    method: "POST",
    body,
    signal,
  });
}


export function getProject(projectId: string, signal?: AbortSignal): Promise<ProjectView> {
  return requestJson<ProjectView>(projectPath(projectId), { signal });
}


export async function getProjectPdf(
  projectId: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const response = await request(projectPath(projectId, "/pdf"), { signal });
  if (!response.ok) {
    throw await readError(response);
  }
  return response.blob();
}


export function generateProject(
  projectId: string,
  input: GenerationRequest,
  signal?: AbortSignal,
): Promise<GenerationResponse> {
  return requestJson<GenerationResponse>(projectPath(projectId, "/generate"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
    signal,
  });
}


export function auditProject(
  projectId: string,
  input: DeepAuditRequest,
  signal?: AbortSignal,
): Promise<DeepAuditResponse> {
  return requestJson<DeepAuditResponse>(projectPath(projectId, "/audit"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
    signal,
  });
}
