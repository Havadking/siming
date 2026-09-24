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
  env_file: string | null
  url_pattern: string | null
  health: string | null            // 配置里写的：相对端口的路径或完整 URL
  health_url: string | null        // 解析好的完整地址
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
  health_result: HealthResult | null   // 配了 health 且查过才有
  git: GitInfo | null                  // 不是 git 仓库 / git 没装 → null
}

export interface HealthResult {
  ok: boolean
  detail: string          // "200" / "HTTP 503" / "连接被拒绝" / "超时 3s"
  checked_at: number
  latency_ms: number | null
}

export interface GitInfo {
  branch: string | null   // detached 时是短 sha
  detached: boolean
  dirty: number           // 未提交条数（改动 + 未跟踪）
  ahead: number
  behind: number
  upstream: string | null
  commit_at: number | null
  commit_msg: string | null
  error: string | null
  incoming: Incoming[]            // 远端有、本地还没合进来的：上游落后 + origin/claude/* 云端分支
  fetched_at: number | null       // 面板上次成功 fetch 的时间
  fetch_error: string | null
}

export interface Incoming {
  ref: string                     // origin/claude/xxx 或上游 origin/main
  sha: string
  kind: 'upstream' | 'cloud'
  count: number                   // 还没进 HEAD 的非合并提交数
  at: number | null               // 其中最新一条的时间
  subjects: string[]              // 最多 8 条，新的在前
}

export interface MergeResult {
  merged: string[]
  failed: { ref: string; message: string; conflicts: string[] } | null
  pushed: boolean
  push_error: string | null
  git: GitInfo | null
}

export interface ProjectsResponse {
  projects: Project[]
  errors: string[]
  groups: string[]          // 分组顺序（含空组）
}

/** 表单里的一个项目，形状就是 projects.yaml 里的一项。空字符串 = 不写。 */
export interface ProjectForm {
  id: string
  name: string
  cwd: string
  cmd: string
  port: string
  group: string
  autostart: boolean
  restart: 'on-failure' | 'never'
  url: string
  url_pattern: string
  health: string
  env_file: string
  env: Record<string, string>
}

export interface Detected {
  cwd: string
  kind: 'node' | 'python' | 'unknown'
  name: string
  id: string
  cmd: string | null
  candidates: string[]
  port: number | null
  notes: string[]
}

export interface Validation { hard: string[]; soft: string[] }

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
const json = <T,>(path: string, method: string, body: unknown) =>
  req<T>(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  projects: () => req<ProjectsResponse>('/api/projects'),
  start: (id: string) => post(`/api/projects/${id}/start`),
  stop: (id: string) => post(`/api/projects/${id}/stop`),
  restart: (id: string) => post(`/api/projects/${id}/restart`),
  startAll: () => post('/api/projects/start-all'),
  stopAll: () => post('/api/projects/stop-all'),
  openFolder: (id: string) => post(`/api/projects/${id}/open-folder`),
  openEditor: (id: string) => post(`/api/projects/${id}/open-editor`),
  openTerminal: (id: string) => post(`/api/projects/${id}/open-terminal`),
  openLogFile: (id: string) => post(`/api/projects/${id}/open-log-file`),
  logStreamUrl: (id: string) => `/api/projects/${id}/logs/stream`,
  // 云端分支：拉取 / 合并 / 忽略
  gitFetch: (id: string) => post(`/api/projects/${id}/git/fetch`) as Promise<{ error: string | null; git: GitInfo | null }>,
  gitMerge: (id: string, refs: string[], push: boolean) => json<MergeResult>(`/api/projects/${id}/git/merge`, 'POST', { refs, push }),
  gitIgnore: (id: string, ref: string, sha: string) => json<{ git: GitInfo | null }>(`/api/projects/${id}/git/ignore`, 'POST', { ref, sha }),
  // 面板自己
  panel: () => req<{ pid: number; started_at: number; version: string }>('/api/panel'),
  restartPanel: () => post('/api/panel/restart') as Promise<{ ok: boolean; pid: number }>,
  // v0.2：界面编辑，写回 projects.yaml
  validate: (project: ProjectForm, editing: string | null) => json<Validation>('/api/projects/validate', 'POST', { project, editing }),
  create: (p: ProjectForm) => json<{ id: string; soft: string[] }>('/api/projects', 'POST', p),
  update: (id: string, p: ProjectForm) => json<{ id: string; soft: string[] }>(`/api/projects/${id}`, 'PUT', p),
  remove: (id: string) => req<unknown>(`/api/projects/${id}`, { method: 'DELETE' }),
  order: (order: { id: string; group: string | null }[]) => json<unknown>('/api/projects/order', 'POST', order),
  addGroup: (name: string) => json<unknown>('/api/groups', 'POST', { name }),
  renameGroup: (old: string, name: string) => json<unknown>(`/api/groups/${encodeURIComponent(old)}`, 'PUT', { name }),
  deleteGroup: (name: string) => req<unknown>(`/api/groups/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  orderGroups: (names: string[]) => json<unknown>('/api/groups/order', 'POST', names),
  detect: (cwd: string) => req<Detected>(`/api/detect?cwd=${encodeURIComponent(cwd)}`),
  pickFolder: (initial: string | null) => json<{ path: string | null; detected: Detected | null }>('/api/pick-folder', 'POST', { initial }),
  // 小工具
  tools: () => req<{ tools: Tool[] }>('/api/tools'),
  addTool: (data: { name: string; file: string; desc?: string | null }) =>
    json<Tool>('/api/tools', 'POST', data),
  pickToolFile: () => json<{ path: string | null }>('/api/tools/pick-file', 'POST', {}),
  toolUrl: (id: string) => `/view-tool/${encodeURIComponent(id)}`,
}

export interface Tool {
  id: string
  name: string
  file: string
  desc: string | null
  error: string | null
}

