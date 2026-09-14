const API_BASE = '/api/v1'

export function getToken(): string | null {
  return localStorage.getItem('tb_token')
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem('tb_token', token)
  else localStorage.removeItem('tb_token')
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/**
 * Pull the message out of an already-parsed FastAPI error body.
 *
 * Separate from reading the response on purpose: a Response body can only be
 * read once, so a caller that has already parsed the JSON passes it in rather
 * than asking for it again.
 */
function errorMessage(status: number, data: any): string {
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg
  if (detail) return JSON.stringify(detail)
  return `HTTP ${status}`
}

/** Read an error body from a response nothing has consumed yet. */
async function parseError(res: Response): Promise<string> {
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) {
    try {
      return errorMessage(res.status, await res.json())
    } catch {
      /* fall through */
    }
  }
  return `HTTP ${res.status}`
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> | undefined) }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (options.body && typeof options.body === 'string' && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') || ''
  let data: any = null
  if (ct.includes('application/json')) data = await res.json()
  // Read the message out of `data`, not the response: the body above has
  // already been consumed, so calling res.json() again throws and every
  // server-supplied message ("Device with unique_id 'dev-0004' already
  // exists") was being replaced with a bare "HTTP 409".
  if (!res.ok) throw new ApiError(res.status, ct.includes('application/json')
    ? errorMessage(res.status, data)
    : `HTTP ${res.status}`)
  return data as T
}

export async function downloadFile(path: string, filename: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  })
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  // The anchor and the object URL are both released on the next tick rather
  // than immediately. Firefox cancels a download whose object URL is revoked in
  // the same task as the click that started it, and mobile browsers can be just
  // as literal about the anchor itself — removing it synchronously is enough
  // for some to drop a download that has only just been handed to the OS.
  setTimeout(() => {
    a.remove()
    URL.revokeObjectURL(url)
  }, 0)
}

export function uploadFile(
  path: string,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
): Promise<any> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', `${API_BASE}${path}`)
    request.setRequestHeader('Authorization', `Bearer ${getToken()}`)
    request.responseType = 'json'
    request.upload.onprogress = (event) => onProgress?.(event.loaded, event.lengthComputable ? event.total : 0)
    request.onerror = () => reject(new ApiError(0, 'Network error while uploading the import file'))
    request.onload = () => {
      const body = request.response
      if (request.status < 200 || request.status >= 300) {
        reject(new ApiError(request.status, errorMessage(request.status, body)))
        return
      }
      resolve(body)
    }
    const form = new FormData()
    form.append('file', file)
    request.send(form)
  })
}
