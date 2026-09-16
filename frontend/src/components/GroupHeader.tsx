import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronDown, FolderPlus, Pencil, Trash2 } from 'lucide-react'
import { Menu } from './ui'

/** 一行输入框：Enter 提交、Esc / 失焦取消。改名和新建分组共用。 */
function InlineInput({ initial, placeholder, onSubmit, onCancel }: {
  initial: string
  placeholder?: string
  onSubmit: (v: string) => void
  onCancel: () => void
}) {
  const [v, setV] = useState(initial)
  const ref = useRef<HTMLInputElement>(null)
  const done = useRef(false)   // Enter 之后紧跟着 blur，别提交两次
  useEffect(() => { ref.current?.focus(); ref.current?.select() }, [])
  const submit = () => {
    if (done.current) return
    done.current = true
    const t = v.trim()
    if (t && t !== initial) onSubmit(t); else onCancel()
  }
  return (
    <input ref={ref} className="ginput" value={v} placeholder={placeholder} spellCheck={false}
      onChange={(e) => setV(e.target.value)}
      onBlur={submit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); submit() }
        else if (e.key === 'Escape') { e.preventDefault(); onCancel() }
      }} />
  )
}

/** 组标题：占 grid 整行。点名字折叠，⋯ 里改名 / 上下移 / 删除；拖卡片到上面换组。 */
export function GroupHeader({ name, count, liveN, collapsed, isOther, canUp, canDown, dropzone, onToggle, onRename, onMove, onDelete, onDragEnter }: {
  name: string
  count: number
  liveN: number
  collapsed: boolean
  isOther: boolean
  canUp: boolean
  canDown: boolean
  dropzone: boolean
  onToggle: () => void
  onRename: (v: string) => void
  onMove: (dir: -1 | 1) => void
  onDelete: () => void
  onDragEnter: () => void
}) {
  const [editing, setEditing] = useState(false)
  return (
    <div className={`gtitle${collapsed ? ' closed' : ''}${dropzone ? ' dropzone' : ''}`}
      onDragEnter={dropzone ? (e) => { e.preventDefault(); onDragEnter() } : undefined}
      onDragOver={dropzone ? (e) => { e.preventDefault() } : undefined}>
      {editing
        ? <InlineInput initial={name} onSubmit={(v) => { setEditing(false); onRename(v) }} onCancel={() => setEditing(false)} />
        : (
          <button type="button" className="gtoggle" onClick={onToggle} aria-expanded={!collapsed}>
            <ChevronDown />{name}
            <span className="gsum">{collapsed ? `${count} 个 · 在线 ${liveN}` : count === 0 ? '空' : count}</span>
          </button>
        )}
      {!isOther && !editing && (
        <span className="gmenu">
          <Menu title="分组" items={[
            { label: '重命名', icon: <Pencil />, onClick: () => setEditing(true) },
            { label: '上移', icon: <ArrowUp />, disabled: !canUp, onClick: () => onMove(-1) },
            { label: '下移', icon: <ArrowDown />, disabled: !canDown, onClick: () => onMove(1) },
            { label: '删除分组', icon: <Trash2 />, danger: true, onClick: onDelete },
          ]} />
        </span>
      )}
    </div>
  )
}

/** grid 末尾的「+ 新建分组」一行，点了原地变输入框。 */
export function NewGroupRow({ onCreate }: { onCreate: (name: string) => void }) {
  const [editing, setEditing] = useState(false)
  if (editing) {
    return (
      <div className="gtitle newgroup">
        <InlineInput initial="" placeholder="分组名，Enter 确定" onSubmit={(v) => { setEditing(false); onCreate(v) }} onCancel={() => setEditing(false)} />
      </div>
    )
  }
  return (
    <button type="button" className="gtitle newgroup" onClick={() => setEditing(true)}>
      <FolderPlus />新建分组
    </button>
  )
}
