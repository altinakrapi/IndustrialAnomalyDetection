const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1'
const TOKEN_KEY = 'industrial-anomaly-token'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(message: string, status: number, detail: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export function isManualConfirmationRequired(error: unknown): error is ApiError & { detail: { code: 'manual_confirmation_required'; message: string; score: number; threshold: number } } {
  if (!(error instanceof ApiError) || error.status !== 409 || typeof error.detail !== 'object' || error.detail === null) return false
  const detail = error.detail as Record<string, unknown>
  return detail.code === 'manual_confirmation_required' && typeof detail.message === 'string'
    && typeof detail.score === 'number' && typeof detail.threshold === 'number'
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = body?.detail
    const message = typeof detail === 'string'
      ? detail
      : typeof detail === 'object' && detail !== null && 'message' in detail && typeof detail.message === 'string'
        ? detail.message
        : `Request failed (${response.status})`
    if (response.status === 401) window.dispatchEvent(new Event('session-expired'))
    throw new ApiError(message, response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function requestAllPages<T>(path: string): Promise<T[]> {
  const pageSize = 500
  const results: T[] = []

  for (let offset = 0; ; offset += pageSize) {
    const page = await request<T[]>(`${path}?limit=${pageSize}&offset=${offset}`)
    results.push(...page)
    if (page.length < pageSize) return results
  }
}

export async function download(path: string, filename: string) {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}${path}`, { headers })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    if (response.status === 401) window.dispatchEvent(new Event('session-expired'))
    throw new Error(body?.detail ?? `Download failed (${response.status})`)
  }
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
