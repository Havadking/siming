import { useRef } from 'react'
import { CloudDownload, CloudUpload, Code2, Copy, ExternalLink, FileText, FolderOpen, GitBranch, GripVertical, Pencil, Play, RotateCw, ScrollText, Square, Terminal, Trash2 } from 'lucide-react'
import type { Project } from '../api'
import type { History } from '../hooks/useHistory'
import { ago, bytes, duration } from '../lib/format'
import { Sparkline } from './Sparkline'
import { STATUS_LABEL, StatusDot } from './StatusDot'
import { Button, Menu } from './ui'

export type Action = 'start' | 'stop' | 'restart'

const PENDING_LABEL: Record<Action, string> = { start: '正在启动…', stop: '正在停止…', restart: '正在重启…' }

export type MenuKey = 'folder' | 'terminal' | 'editor' | 'copy' | 'logfile' | 'edit' | 'delete' | 'sync' | 'push'

/** 卡片底下的一行提示：失败红色，成功（如推送完）绿色 */
export interface CardNote { text: string; ok?: boolean }

export function ProjectCard({ p, pending, note, pushing, onAction, onLogs, onMenu, now, history, drag }: {
  p: Project
  pending: Action | null
  note: CardNote | null
  pushing: boolean
  onAction: (a: Action) => void
  onLogs: () => void
  onMenu: (k: MenuKey) => void
  now: number
  history?: History
  drag?: {
    dragging: boolean
    onStart: (e: React.DragEvent) => void
    onEnter: () => void
    onEnd: () => void
  }
}) {
  const el = useRef<HTMLDivElement>(null)
  const s = p.status
  const live = s === 'running' || s === 'starting' || s === 'unhealthy' || s === 'external' || s === 'restarting'
  const canOpen = (s === 'running' || s === 'external') && !!p.url
  const isErr = s === 'error'

  let line2: string
  if (pending) line2 = PENDING_LABEL[pending]
  else if (s === 'starting') {
    const hr = p.health_result
    line2 = `启动中 · 已等 ${duration(p.uptime, { seconds: true })}${p.health_url && hr && !hr.ok ? ` · 健康检查 ${hr.detail}` : ''}`
  }
  else if (s === 'running' || s === 'external') line2 = `${STATUS_LABEL[s]} · ${duration(p.uptime)}`
  else if (s === 'unhealthy') {
    const hr = p.health_result
    line2 = p.health_url && hr && !hr.ok
      ? `健康检查没过：${hr.detail} · ${duration(p.uptime)}`
      : `进程在但端口 ${p.port} 没开 · ${duration(p.uptime)}`
  }
  else if (s === 'restarting') line2 = `${Math.max(0, Math.ceil((p.restart_due ?? now) - now))}s 后重启 · 第 ${p.restart_count} 次`
  else if (s === 'exited') line2 = `退出码 ${p.exit_code} · ${ago(p.exited_at)}`
  else if (s === 'crashed') line2 = `10 分钟内失败 ${p.failures} 次，已停止自动重启`
  else if (s === 'error') line2 = p.error ?? '配置错误'
  else line2 = p.exit_code != null && p.exited_at ? `已停止 · ${ago(p.exited_at)}` : '已停止'

  const rssHist = history?.rss ?? []
  const cpuHist = history?.cpu ?? []
  const span = (h: typeof rssHist) => (h.length >= 2 ? `最近 ${duration(now - h[0].t)}` : '')
  const vals = (h: typeof rssHist) => h.map((x) => x.v)
  const line3 = live
    ? (
      <>
        <span>{bytes(p.rss)}</span>
        <Sparkline data={rssHist} className="mem"
          title={rssHist.length >= 2 ? `内存 ${span(rssHist)}：${bytes(Math.min(...vals(rssHist)))} – ${bytes(Math.max(...vals(rssHist)))}` : undefined} />
        {p.cpu != null && (
          <>
            <span className="sep">·</span>
            <span>{p.cpu}%</span>
            <Sparkline data={cpuHist} floor className="cpu"
              title={cpuHist.length >= 2 ? `CPU ${span(cpuHist)}：峰值 ${Math.max(...vals(cpuHist))}%` : undefined} />
          </>
        )}
        {p.restart_count > 0 && <><span className="sep">·</span><span>重启 {p.restart_count}</span></>}
        {p.health_result?.ok && p.health_result.latency_ms != null && (
          <><span className="sep">·</span><span title={`健康检查 ${p.health_url} → ${p.health_result.detail}`}>健康 {p.health_result.latency_ms}ms</span></>
        )}
      </>
    )
    : p.exit_code != null && s === 'stopped' ? `上次退出 ${p.exit_code}` : ' '

  const g = p.git
  const incomingN = g?.incoming.reduce((n, i) => n + i.count, 0) ?? 0

  return (
    <div ref={el} className={`card proj${isErr ? ' cfg-err' : ''}${pending ? ' busy' : ''}${drag?.dragging ? ' dragging' : ''}`}
      title={`${p.cwd}\n$ ${p.cmd}`}
      onDragEnter={drag ? (e) => { e.preventDefault(); drag.onEnter() } : undefined}
      onDragOver={drag ? (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' } : undefined}>
      <div className="head">
        <StatusDot status={s} />
        <h3 className="name" title={p.name}>{p.name}</h3>
        {p.port && (
          canOpen
            ? <a className="port mono" href={p.url!} target="_blank" rel="noreferrer" title={`打开 ${p.url}`}>:{p.port}<ExternalLink /></a>
            : <span className="port mono off">:{p.port}</span>
        )}
      </div>
      {p.desc && <div className="desc" title={p.desc}>{p.desc}</div>}
      <div className={`l2 ${isErr ? 'bad' : ''}`}>{line2}</div>
      <div className="l3 mono">{line3}</div>
      {g && (
        <div className="l4 git mono" title={g.error ?? (g.commit_msg ? `最近提交：${g.commit_msg}` : undefined)}>
          <GitBranch />
          <span className={`branch${g.detached ? ' detached' : ''}`}>{g.branch ?? '?'}</span>
          {g.dirty > 0 && <span className="dirty" title={`${g.dirty} 处未提交改动`}>●{g.dirty}</span>}
          {g.ahead > 0 && (
            <button type="button" className="ab outgoing" disabled={pushing} onClick={() => onMenu('push')}
              title={[`本地领先 ${g.upstream ?? '上游'} ${g.ahead} 个提交，点一下推送`, ...g.outgoing.map((s) => `· ${s}`)].join('\n')}>
              {pushing ? '推送中…' : `↑${g.ahead}`}
            </button>
          )}
          {g.behind > 0 && <span className="ab" title={g.upstream ? `落后 ${g.upstream}` : undefined}>↓{g.behind}</span>}
          {g.incoming.length > 0 && (
            <button type="button" className="incoming" onClick={() => onMenu('sync')}
              title={[`GitHub 上有 ${incomingN} 个提交还没合进本地，点开合并`,
                ...g.incoming.map((i) => `${i.ref.replace(/^origin\//, '')}：${i.subjects[0] ?? ''}`)].join('\n')}>
              <CloudDownload />{incomingN}
            </button>
          )}
          {g.error
            ? <span className="bad">{g.error}</span>
            : g.commit_at != null && <><span className="sep">·</span><span>{ago(g.commit_at)}</span></>}
        </div>
      )}
      {note && <div className={note.ok ? 'act-ok' : 'act-err'}>{note.text}</div>}
      <div className="actions">
        {!isErr && (live
          ? <Button size="sm" disabled={!!pending} onClick={() => onAction('stop')}><Square />停止</Button>
          : <Button size="sm" variant="primary" disabled={!!pending} onClick={() => onAction('start')}><Play />{s === 'crashed' ? '重新启动' : '启动'}</Button>
        )}
        {(s === 'running' || s === 'unhealthy') && (
          <Button size="sm" variant="ghost" disabled={!!pending} onClick={() => onAction('restart')}><RotateCw />重启</Button>
        )}
        {!isErr && (
          <Button size="sm" variant="ghost" disabled={!p.logs_available && s === 'external'} onClick={onLogs}
            title={s === 'external' && !p.logs_available ? '在终端里起的，日志看那边' : '查看日志'}>
            <ScrollText />日志
          </Button>
        )}
        <span className="grow" />
        {drag && (
          <span className="grip" draggable title="拖动排序 / 换组" aria-label="拖动排序"
            onDragStart={(e) => {
              e.dataTransfer.effectAllowed = 'move'
              e.dataTransfer.setData('text/plain', p.id)
              if (el.current) e.dataTransfer.setDragImage(el.current, 20, 20)
              drag.onStart(e)
            }}
            onDragEnd={drag.onEnd}>
            <GripVertical />
          </span>
        )}
        <Menu items={[
          { label: '编辑', icon: <Pencil />, onClick: () => onMenu('edit') },
          { label: '打开目录', icon: <FolderOpen />, onClick: () => onMenu('folder') },
          { label: '在终端打开', icon: <Terminal />, onClick: () => onMenu('terminal') },
          { label: '在 VS Code 打开', icon: <Code2 />, onClick: () => onMenu('editor') },
          ...(g && !g.error ? [{ label: '同步云端改动…', icon: <CloudDownload />, onClick: () => onMenu('sync') }] : []),
          ...(g && !g.error && !g.detached ? [{ label: '推送到远端', icon: <CloudUpload />, disabled: pushing, onClick: () => onMenu('push') }] : []),
          { label: '打开日志文件', icon: <FileText />, onClick: () => onMenu('logfile') },
          { label: '复制命令', icon: <Copy />, onClick: () => onMenu('copy') },
          { label: '删除', icon: <Trash2 />, danger: true, onClick: () => onMenu('delete') },
        ]} />
      </div>
      <div className="hint mono">{p.cwd}<br />$ {p.cmd}</div>
    </div>
  )
}
