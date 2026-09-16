export type Status =
  | 'running' | 'starting' | 'unhealthy' | 'stopped' | 'exited'
  | 'restarting' | 'crashed' | 'external' | 'error'

export interface Project {
  id: string
  name: string
  cwd: string
  cmd: string
  port: number | null
  url: string | null
  autostart: boolean
  restart: 'on-failure' | 'never'
  env: Record<string, string>
  group: string | null
  error: string | null
  status: Status
  pid: number | null
  uptime: number | null
  rss: number | null
  cpu: number | null
  restart_count: number
  exit_code: number | null
  exited_at: number | null
  restart_due: number | null
  failures: number
  logs_available: boolean
}

export interface ProjectsResponse {
  projects: Project[]
  errors: string[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) {
    let msg = r.statusText
    try {
      const body = await r.json()
      if (typeof body?.detail === 'string') msg = body.detail
    } catch { /* not json */ }
    throw new ApiError(r.status, msg)
  }
  return r.json() as Promise<T>
}

const post = (path: string) => req<unknown>(path, { method: 'POST' })

export const api = {
  projects: () => req<ProjectsResponse>('/api/projects'),
  start: (id: string) => post(`/api/projects/${id}/start`),
  stop: (id: string) => post(`/api/projects/${id}/stop`),
  restart: (id: string) => post(`/api/projects/${id}/restart`),
  startAll: () => post('/api/projects/start-all'),
  stopAll: () => post('/api/projects/stop-all'),
  openFolder: (id: string) => post(`/api/projects/${id}/open-folder`),
  openEditor: (id: string) => post(`/api/projects/${id}/open-editor`),
  openLogFile: (id: string) => post(`/api/projects/${id}/open-log-file`),
  logStreamUrl: (id: string) => `/api/projects/${id}/logs/stream`,
}
