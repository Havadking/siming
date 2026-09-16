import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Moon, Play, Square, Sun } from 'lucide-react'
import { api, type Project } from './api'
import { LogDrawer } from './components/LogDrawer'
import { ProjectCard, type Action } from './components/ProjectCard'
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

export default function App() {
  const { dark, toggle } = useTheme()
  const { data, error: pollError, refresh } = usePolling(api.projects, 2000)
  const [pending, setPending] = useState<Record<string, Action | null>>({})
  const [cardErr, setCardErr] = useState<Record<string, string | null>>({})
  const [logId, setLogId] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const errTimers = useRef<Record<string, number>>({})

  // 倒计时 / 「几分钟前」这类文字要秒级刷新
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [])

  const projects = data?.projects ?? []
  const cfgErrors = data?.errors ?? []

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

  const menu = useCallback(async (p: Project, k: 'folder' | 'editor' | 'copy' | 'logfile') => {
    try {
      if (k === 'folder') await api.openFolder(p.id)
      else if (k === 'editor') await api.openEditor(p.id)
      else if (k === 'logfile') await api.openLogFile(p.id)
      else await navigator.clipboard.writeText(`cd ${p.cwd}\n${p.cmd}`)
    } catch (e) {
      showErr(p.id, e instanceof Error ? e.message : String(e))
    }
  }, [showErr])

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
    document.title = data ? `${online}/${projects.length} 在线 · devpanel` : 'devpanel'
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
    if (rest) named.push(['其他', rest])
    return named
  }, [projects])

  const logProject = logId ? projects.find((p) => p.id === logId) ?? null : null
  useEffect(() => { if (logId && data && !logProject) setLogId(null) }, [logId, data, logProject])

  return (
    <div className={`app${logProject ? ' with-drawer' : ''}`}>
      <header className="top">
        <h1>本地项目</h1>
        {data && (
          <span className="stats">
            {projects.length} 个 · 在线 <b>{online}</b> · 内存 <b>{bytes(rss)}</b>
          </span>
        )}
        <span className="grow" />
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
          <div className="empty"><b>清单是空的</b>在 <code>projects.yaml</code> 里加项目，保存后 2 秒内出现在这里。</div>
        )}
        {groups.map(([g, ps]) => (
          <section key={g} className="group">
            {groups.length > 1 && <h2 className="gtitle">{g}</h2>}
            <div className="grid">
              {ps.map((p) => (
                <ProjectCard key={p.id} p={p} now={now}
                  pending={pending[p.id] ?? null} error={cardErr[p.id] ?? null}
                  onAction={(a) => { void act(p.id, a) }}
                  onLogs={() => setLogId(p.id)}
                  onMenu={(k) => { void menu(p, k) }} />
              ))}
            </div>
          </section>
        ))}
      </main>

      {logProject && <LogDrawer id={logProject.id} name={logProject.name} onClose={() => setLogId(null)} />}
    </div>
  )
}
