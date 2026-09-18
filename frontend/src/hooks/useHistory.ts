import { useMemo, useRef } from 'react'
import type { Project } from '../api'
import type { Sample } from '../components/Sparkline'

const MAX_AGE = 600     // 最近 10 分钟
const LIVE = new Set(['running', 'starting', 'unhealthy', 'external'])

export interface History { pid: number | null; rss: Sample[]; cpu: Sample[] }

/**
 * 每次轮询把 rss / cpu 塞进各项目的 ring buffer（2s 一个点 ≈ 300 个），不落盘。
 * 项目不在跑、或 pid 变了（重启是同步的，轮询看不到中间那个「已停止」）就把它的历史清掉，从头画。
 * 同一个 data 对象只采一次（StrictMode 会把 useMemo 跑两遍）。
 */
export function useHistory(projects: Project[] | undefined): Map<string, History> {
  const store = useRef(new Map<string, History>())
  const last = useRef<Project[] | undefined>(undefined)
  return useMemo(() => {
    if (!projects || projects === last.current) return store.current
    last.current = projects
    const now = Date.now() / 1000
    const seen = new Set<string>()
    for (const p of projects) {
      seen.add(p.id)
      if (!LIVE.has(p.status) || p.rss == null) { store.current.delete(p.id); continue }
      let h = store.current.get(p.id)
      if (!h || h.pid !== p.pid) { h = { pid: p.pid, rss: [], cpu: [] }; store.current.set(p.id, h) }
      h.rss.push({ t: now, v: p.rss })
      if (p.cpu != null) h.cpu.push({ t: now, v: p.cpu })
      const cut = now - MAX_AGE
      while (h.rss.length && h.rss[0].t < cut) h.rss.shift()
      while (h.cpu.length && h.cpu[0].t < cut) h.cpu.shift()
    }
    for (const id of [...store.current.keys()]) if (!seen.has(id)) store.current.delete(id)
    return store.current
  }, [projects])
}
