import { useRef } from 'react'
import { Code2, Copy, ExternalLink, FileText, FolderOpen, GripVertical, Pencil, Play, RotateCw, ScrollText, Square, Trash2 } from 'lucide-react'
import type { Project } from '../api'
import { ago, bytes, duration } from '../lib/format'
import { STATUS_LABEL, StatusDot } from './StatusDot'
import { Button, Menu } from './ui'

export type Action = 'start' | 'stop' | 'restart'

const PENDING_LABEL: Record<Action, string> = { start: '正在启动…', stop: '正在停止…', restart: '正在重启…' }

export type MenuKey = 'folder' | 'editor' | 'copy' | 'logfile' | 'edit' | 'delete'

export function ProjectCard({ p, pending, error, onAction, onLogs, onMenu, now, drag }: {
  p: Project
  pending: Action | null
  error: string | null
  onAction: (a: Action) => void
  onLogs: () => void
  onMenu: (k: MenuKey) => void
  now: number
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
  else if (s === 'starting') line2 = `启动中 · 已等 ${duration(p.uptime, { seconds: true })}`
  else if (s === 'running' || s === 'external') line2 = `${STATUS_LABEL[s]} · ${duration(p.uptime)}`
  else if (s === 'unhealthy') line2 = `进程在但端口 ${p.port} 没开 · ${duration(p.uptime)}`
  else if (s === 'restarting') line2 = `${Math.max(0, Math.ceil((p.restart_due ?? now) - now))}s 后重启 · 第 ${p.restart_count} 次`
  else if (s === 'exited') line2 = `退出码 ${p.exit_code} · ${ago(p.exited_at)}`
  else if (s === 'crashed') line2 = `10 分钟内失败 ${p.failures} 次，已停止自动重启`
  else if (s === 'error') line2 = p.error ?? '配置错误'
  else line2 = p.exit_code != null && p.exited_at ? `已停止 · ${ago(p.exited_at)}` : '已停止'

  const line3 = live
    ? [bytes(p.rss), p.cpu != null ? `${p.cpu}%` : null, p.restart_count ? `重启 ${p.restart_count}` : null].filter(Boolean).join(' · ')
    : p.exit_code != null && s === 'stopped' ? `上次退出 ${p.exit_code}` : ' '

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
      <div className={`l2 ${isErr ? 'bad' : ''}`}>{line2}</div>
      <div className="l3 mono">{line3}</div>
      {error && <div className="act-err">{error}</div>}
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
          { label: '在 VS Code 打开', icon: <Code2 />, onClick: () => onMenu('editor') },
          { label: '打开日志文件', icon: <FileText />, onClick: () => onMenu('logfile') },
          { label: '复制命令', icon: <Copy />, onClick: () => onMenu('copy') },
          { label: '删除', icon: <Trash2 />, danger: true, onClick: () => onMenu('delete') },
        ]} />
      </div>
      <div className="hint mono">{p.cwd}<br />$ {p.cmd}</div>
    </div>
  )
}
