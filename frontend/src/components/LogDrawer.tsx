import { useEffect, useRef, useState } from 'react'
import { ArrowDown, Eraser, FileText, X } from 'lucide-react'
import { api } from '../api'
import { useLogStream } from '../hooks/useLogStream'
import { Button } from './ui'

export function LogDrawer({ id, name, onClose }: { id: string; name: string; onClose: () => void }) {
  const { lines, connected, clear } = useLogStream(api.logStreamUrl(id))
  const box = useRef<HTMLDivElement>(null)
  const [follow, setFollow] = useState(true)

  // 自动滚到底；用户往上滚了就停住，底部出现「回到最新」
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight
  }, [lines, follow])

  const onScroll = () => {
    const el = box.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    if (atBottom !== follow) setFollow(atBottom)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside className="drawer" aria-label={`${name} 的日志`}>
      <div className="dhead">
        <span className={`sdot ${connected ? 'ok' : 'neutral'}`} title={connected ? '实时' : '未连接'} />
        <h2>日志：{name}</h2>
        <span className="cnt mono">{lines.length} 行</span>
        <button type="button" className="btn ghost sm iconbtn" onClick={onClose} aria-label="关闭"><X /></button>
      </div>
      <div className="dbody mono" ref={box} onScroll={onScroll}>
        {lines.length === 0
          ? <div className="dempty">还没有输出</div>
          : lines.map((l, i) => <div key={i} className={lineClass(l)}>{l || ' '}</div>)}
      </div>
      {!follow && (
        <button type="button" className="btn sm tolatest" onClick={() => setFollow(true)}><ArrowDown />回到最新</button>
      )}
      <div className="dfoot">
        <Button size="sm" variant="ghost" onClick={clear}><Eraser />清空显示</Button>
        <Button size="sm" variant="ghost" onClick={() => { void api.openLogFile(id).catch(() => {}) }}><FileText />打开日志文件</Button>
      </div>
    </aside>
  )
}

function lineClass(l: string): string | undefined {
  if (l.startsWith('=====')) return 'sep'
  if (l.startsWith('[devpanel]')) return 'sys'
  if (/\b(error|exception|traceback|fatal)\b/i.test(l)) return 'err'
  if (/\b(warn|warning)\b/i.test(l)) return 'warn'
  return undefined
}
