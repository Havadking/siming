import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, ChevronDown, Moon, Play, Plus, Square, Sun } from 'lucide-react'
import { api, type Project } from './api'
import { LogDrawer } from './components/LogDrawer'
import { ProjectCard, type Action, type MenuKey } from './components/ProjectCard'
import { ProjectDialog } from './components/ProjectDialog'
import { Button } from './components/ui'
import { usePolling } from './hooks/usePolling'
import { bytes } from './lib/format'

type Theme = 'light' | 'dark' | 'system'

function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try { const v = localStorage.getItem('theme'); return v === 'light' || v === 'dark' ? v : 'system' } catch { return 'system' }
  })
  const [systemDark, setSystemDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches)
  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const fn = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', fn)
    return () => mq.removeEventListener('change', fn)
  }, [])
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try { theme === 'system' ? localStorage.removeItem('theme') : localStorage.setItem('theme', theme) } catch { /* ignore */ }
  }, [theme])
  const dark = theme === 'dark' || (theme === 'system' && systemDark)
  const toggle = () => setTheme(dark ? 'light' : 'dark')
  return { dark, toggle }
}

const LIVE = new Set(['running', 'starting', 'unhealthy', 'external', 'restarting'])
const OTHER = '其他'

function useCollapsed() {
  const [set, setSet] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem('collapsed-groups') ?? '[]') as string[]) } catch { return new Set() }
  })
  const toggle = (g: string) => setSet((prev) => {
    const next = new Set(prev)
    if (next.has(g)) next.delete(g); else next.add(g)
    try { localStorage.setItem('collapsed-groups', JSON.stringify([...next])) } catch { /* ignore */ }
    return next
  })
  return { collapsed: set, toggle }
}

type OrderEntry = { id: string; group: string | null }

export default function App() {
  const { dark, toggle } = useTheme()
  const { data, error: pollError, refresh } = usePolling(api.projects, 2000)
  const [pending, setPending] = useState<Record<string, Action | null>>({})
  const [cardErr, setCardErr] = useState<Record<string, string | null>>({})
  const [logId, setLogId] = useState<string | null>(null)
  const [dialog, setDialog] = useState<{ editing: Project | null } | null>(null)
  const { collapsed, toggle: toggleGroup } = useCollapsed()
  // 拖拽中：本地顺序覆盖服务端顺序，松手后写回
  const [dragId, setDragId] = useState<string | null>(null)
  const [localOrder, setLocalOrder] = useState<OrderEntry[] | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const errTimers = useRef<Record<string, number>>({})

  // 倒计时 / 「几分钟前」这类文字要秒级刷新
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [])

  const serverProjects = useMemo(() => data?.projects ?? [], [data])
  const cfgErrors = data?.errors ?? []
  const projects = useMemo(() => {
    if (!localOrder) return serverProjects
    const byId = new Map(serverProjects.map((p) => [p.id, p]))
    return localOrder.flatMap((o) => { const p = byId.get(o.id); return p ? [{ ...p, group: o.group }] : [] })
  }, [serverProjects, localOrder])

  // 拿到真实状态后清掉过渡态
  useEffect(() => {
    if (!data) return
    setPending((prev) => {
      let changed = false
      const next = { ...prev }
      for (const p of data.projects) {
        const a = prev[p.id]
        if (!a) continue
        const done = (a === 'stop' && !LIVE.has(p.status))
          || (a === 'start' && LIVE.has(p.status))
          || (a === 'restart' && LIVE.has(p.status) && (p.uptime ?? 99) < 5)
        if (done) { next[p.id] = null; changed = true }
      }
      return changed ? next : prev
    })
  }, [data])

  const showErr = useCallback((id: string, msg: string) => {
    setCardErr((m) => ({ ...m, [id]: msg }))
    if (errTimers.current[id]) clearTimeout(errTimers.current[id])
    errTimers.current[id] = window.setTimeout(() => setCardErr((m) => ({ ...m, [id]: null })), 5000)
  }, [])

  const act = useCallback(async (id: string, a: Action) => {
    setPending((m) => ({ ...m, [id]: a }))
    try {
      await api[a](id)
      void refresh()
    } catch (e) {
      setPending((m) => ({ ...m, [id]: null }))
      showErr(id, e instanceof Error ? e.message : String(e))
    }
  }, [refresh, showErr])

  const remove = useCallback(async (p: Project) => {
    if (LIVE.has(p.status)) {
      if (!confirm(`「${p.name}」还在运行，先停止再删除？`)) return
      await api.stop(p.id)
    } else if (!confirm(`删除「${p.name}」？projects.yaml 里的这一项会被移除，日志文件保留。`)) return
    await api.remove(p.id)
    void refresh()
  }, [refresh])

  const menu = useCallback(async (p: Project, k: MenuKey) => {
    try {
      if (k === 'folder') await api.openFolder(p.id)
      else if (k === 'editor') await api.openEditor(p.id)
      else if (k === 'logfile') await api.openLogFile(p.id)
      else if (k === 'edit') setDialog({ editing: p })
      else if (k === 'delete') await remove(p)
      else await navigator.clipboard.writeText(`cd ${p.cwd}\n${p.cmd}`)
    } catch (e) {
      showErr(p.id, e instanceof Error ? e.message : String(e))
    }
  }, [showErr, remove])

  // ----- 拖拽排序 -----
  const dragStart = useCallback((id: string) => {
    setDragId(id)
    setLocalOrder(serverProjects.map((p) => ({ id: p.id, group: p.group })))
  }, [serverProjects])

  /** 拖到某张卡片上：插到它的位置，组跟它 */
  const dragOverCard = useCallback((targetId: string) => {
    if (!dragId || dragId === targetId) return
    setLocalOrder((prev) => {
      if (!prev) return prev
      const from = prev.findIndex((o) => o.id === dragId)
      const to = prev.findIndex((o) => o.id === targetId)
      if (from < 0 || to < 0) return prev
      const next = prev.slice()
      const [item] = next.splice(from, 1)
      next.splice(to, 0, { ...item, group: prev[to].group })
      return next
    })
  }, [dragId])

  /** 拖到某个组的标题上：挪到这组开头 */
  const dragOverGroup = useCallback((group: string | null) => {
    if (!dragId) return
    setLocalOrder((prev) => {
      if (!prev) return prev
      const from = prev.findIndex((o) => o.id === dragId)
      if (from < 0) return prev
      const cur = prev[from]
      const rest = prev.filter((o) => o.id !== dragId)
      let first = rest.findIndex((o) => (o.group ?? null) === group)
      if (first < 0) first = rest.length
      if ((cur.group ?? null) === group && from === first) return prev
      rest.splice(first, 0, { ...cur, group })
      return rest
    })
  }, [dragId])

  const dragEnd = useCallback(async () => {
    const order = localOrder
    setDragId(null)
    if (!order) return
    const before = serverProjects.map((p) => `${p.id}:${p.group ?? ''}`).join(',')
    const after = order.map((o) => `${o.id}:${o.group ?? ''}`).join(',')
    if (before === after) { setLocalOrder(null); return }
    try {
      await api.order(order)
      await refresh()
    } catch (e) {
      const id = order[0]?.id
      if (id) showErr(id, `排序没保存：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLocalOrder(null)
    }
  }, [localOrder, serverProjects, refresh, showErr])

  const stopAll = async () => {
    if (!confirm('停止所有在跑的项目？')) return
    try { await api.stopAll(); void refresh() } catch { /* 卡片上会各自显示 */ }
  }
  const startAll = async () => {
    try { await api.startAll(); void refresh() } catch { /* ignore */ }
  }

  const online = projects.filter((p) => p.status === 'running' || p.status === 'external').length
  const rss = projects.reduce((s, p) => s + (p.rss ?? 0), 0)
  const anyLive = projects.some((p) => LIVE.has(p.status))
  const anyStartable = projects.some((p) => p.status === 'stopped' || p.status === 'exited' || p.status === 'crashed')

  useEffect(() => {
    document.title = data ? `${online}/${projects.length} 在线 · 司命` : '司命'
  }, [data, online, projects.length])

  const groups = useMemo(() => {
    const m = new Map<string, Project[]>()
    for (const p of projects) {
      const g = p.group ?? ''
      if (!m.has(g)) m.set(g, [])
      m.get(g)!.push(p)
    }
    const named = [...m.entries()].filter(([g]) => g)
    const rest = m.get('')
    if (rest) named.push([OTHER, rest])
    return named
  }, [projects])
  const groupNames = useMemo(() => groups.map(([g]) => g).filter((g) => g !== OTHER), [groups])

  const logProject = logId ? projects.find((p) => p.id === logId) ?? null : null
  useEffect(() => { if (logId && data && !logProject) setLogId(null) }, [logId, data, logProject])

  return (
    <div className={`app${logProject ? ' with-drawer' : ''}`}>
      <header className="top">
        <h1>司命</h1>
        {data && (
          <span className="stats">
            {projects.length} 个 · 在线 <b>{online}</b> · 内存 <b>{bytes(rss)}</b>
          </span>
        )}
        <span className="grow" />
        <Button size="sm" variant="primary" onClick={() => setDialog({ editing: null })}><Plus />新增</Button>
        <Button size="sm" onClick={startAll} disabled={!anyStartable}><Play />全部启动</Button>
        <Button size="sm" onClick={stopAll} disabled={!anyLive}><Square />全部停止</Button>
        <button type="button" className="btn ghost sm iconbtn" onClick={toggle} aria-label="切换深浅色" title="切换深浅色">
          {dark ? <Sun /> : <Moon />}
        </button>
      </header>

      {pollError && <div className="banner bad"><AlertTriangle />连不上面板：{pollError}</div>}
      {cfgErrors.length > 0 && (
        <div className="banner warn">
          <AlertTriangle />
          <div>配置有 {cfgErrors.length} 处错误：<ul>{cfgErrors.map((e) => <li key={e}>{e}</li>)}</ul></div>
        </div>
      )}

      <main className="page">
        {data && projects.length === 0 && (
          <div className="empty"><b>清单是空的</b>点右上角「新增」，或在 <code>projects.yaml</code> 里加项目。</div>
        )}
        <div className="grid">
          {groups.flatMap(([g, ps]) => {
            const groupKey = g === OTHER ? null : g
            const isCollapsed = groups.length > 1 && collapsed.has(g) && !dragId
            const liveN = ps.filter((p) => p.status === 'running' || p.status === 'external').length
            const items: ReactNode[] = []
            if (groups.length > 1) {
              items.push(
                <button key={`h:${g}`} type="button" className={`gtitle${isCollapsed ? ' closed' : ''}${dragId ? ' dropzone' : ''}`}
                  onClick={() => toggleGroup(g)} aria-expanded={!isCollapsed}
                  onDragEnter={dragId ? (e) => { e.preventDefault(); dragOverGroup(groupKey) } : undefined}
                  onDragOver={dragId ? (e) => { e.preventDefault() } : undefined}>
                  <ChevronDown />{g}
                  <span className="gsum">{isCollapsed ? `${ps.length} 个 · 在线 ${liveN}` : ps.length}</span>
                </button>,
              )
            }
            if (!isCollapsed) {
              for (const p of ps) {
                items.push(
                  <ProjectCard key={p.id} p={p} now={now}
                    pending={pending[p.id] ?? null} error={cardErr[p.id] ?? null}
                    onAction={(a) => { void act(p.id, a) }}
                    onLogs={() => setLogId(p.id)}
                    onMenu={(k) => { void menu(p, k) }}
                    drag={{
                      dragging: dragId === p.id,
                      onStart: () => dragStart(p.id),
                      onEnter: () => dragOverCard(p.id),
                      onEnd: () => { void dragEnd() },
                    }} />,
                )
              }
            }
            return items
          })}
        </div>
      </main>

      {logProject && <LogDrawer id={logProject.id} name={logProject.name} onClose={() => setLogId(null)} />}
      {dialog && (
        <ProjectDialog editing={dialog.editing} groups={groupNames}
          onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); void refresh() }} />
      )}
    </div>
  )
}
