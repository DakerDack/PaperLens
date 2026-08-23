import type { ErrorResponse, HealthResponse } from "./types";


const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";


export class ApiClientError extends Error {
  readonly status: number;
  readonly errorCode: string;
  readonly retryable: boolean;

  constructor(status: number, payload: ErrorResponse) {
    super(payload.message);
    this.name = "ApiClientError";
    this.status = status;
    this.errorCode = payload.error_code;
    this.retryable = payload.retryable;
  }
}


async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  const payload: unknown = await response.json();

  if (!response.ok) {
    const error = payload as ErrorResponse;
    throw new ApiClientError(response.status, error);
  }

  return payload as T;
}


export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/api/health", { signal });
}

