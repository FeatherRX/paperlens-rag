import { API_PREFIX } from '../config/api'

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function errorMessage(payload: unknown): string {
  if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
    const detail = payload.detail
    return typeof detail === 'string' ? detail : JSON.stringify(detail)
  }
  return '请求失败，请稍后重试。'
}

export async function requestJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(errorMessage(payload), response.status)
  }
  return payload as T
}
