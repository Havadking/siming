import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Code2, ExternalLink, FolderOpen, Loader2, Plus, X } from 'lucide-react'
import { api, type Tool } from '../api'
import { Button } from './ui'

export function ToolsDropdown() {
  const [open, setOpen] = useState(false)
  const [tools, setTools] = useState<Tool[]>([])
  const [dialogOpen, setDialogOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const reloadTools = async () => {
    try {
      const res = await api.tools()
      setTools(res.tools ?? [])
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    void reloadTools()
  }, [])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="menu" ref={ref}>
      <Button
        size="sm"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title="本地纯 HTML 单页小工具"
      >
        <Code2 />
        <span>小工具</span>
        {tools.length > 0 && <span className="mono text-[11px] text-[var(--mute)]">({tools.length})</span>}
        <ChevronDown style={{ width: 12, height: 12, opacity: 0.6 }} />
      </Button>

      {open && (
        <div className="menu-pop" style={{ minWidth: 240, padding: 4 }}>
          {tools.length === 0 ? (
            <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--faint)' }}>暂未添加小工具</div>
          ) : (
            tools.map((t) => (
              <button
                key={t.id}
                type="button"
                style={{ justifyContent: 'space-between', width: '100%' }}
                onClick={() => {
                  setOpen(false)
                  window.open(api.toolUrl(t.id), '_blank')
                }}
                title={t.desc ? `${t.name}\n${t.desc}\n${t.file}` : `${t.name}\n${t.file}`}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0, overflow: 'hidden' }}>
                  <span className="mono" style={{ fontSize: 11, color: 'var(--faint)' }}>{'{ }'}</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name}</span>
                </span>
                <ExternalLink style={{ width: 12, height: 12, flexShrink: 0, opacity: 0.5 }} />
              </button>
            ))
          )}
          <div style={{ borderTop: '1px solid var(--line-2)', margin: '4px 0 2px' }} />
          <button
            type="button"
            style={{ color: 'var(--accent-ink)', fontWeight: 500 }}
            onClick={() => {
              setOpen(false)
              setDialogOpen(true)
            }}
          >
            <Plus style={{ width: 13, height: 13 }} />
            <span>添加小工具...</span>
          </button>
        </div>
      )}

      {dialogOpen && (
        <AddToolDialog
          onClose={() => setDialogOpen(false)}
          onSaved={async () => {
            setDialogOpen(false)
            await reloadTools()
          }}
        />
      )}
    </div>
  )
}

function AddToolDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [file, setFile] = useState('')
  const [desc, setDesc] = useState('')
  const [picking, setPicking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameRef.current?.focus()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const handlePickFile = async () => {
    setPicking(true)
    setError(null)
    try {
      const res = await api.pickToolFile()
      if (res.path) {
        setFile(res.path)
        if (!name.trim()) {
          const base = res.path.split(/[\\/]/).pop() ?? ''
          setName(base.replace(/\.(html|htm)$/i, ''))
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPicking(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const dropped = e.dataTransfer.files[0]
    if (dropped && (dropped.name.endsWith('.html') || dropped.name.endsWith('.htm'))) {
      // 现代浏览器拖拽的 File 对象在某些场景下没有直接暴露全路径，但若用户直接输入或在支持的本地环境中可以使用
      // 如果没有真实全路径，提示用户点浏览或直接输入
      // 这里如果能获取到路径则使用
      const p = (dropped as unknown as { path?: string }).path || dropped.name
      setFile(p)
      if (!name.trim()) {
        setName(dropped.name.replace(/\.(html|htm)$/i, ''))
      }
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !file.trim()) return
    setSaving(true)
    setError(null)
    try {
      await api.addTool({
        name: name.trim(),
        file: file.trim(),
        desc: desc.trim() || null,
      })
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return createPortal(
    <div
      className="modal-bg"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px 16px',
        overflowY: 'auto',
      }}
      onClick={onClose}
    >
      <div
        className="card modal"
        style={{
          width: 480,
          maxWidth: '100%',
          maxHeight: '90vh',
          boxShadow: 'var(--shadow)',
          margin: 'auto',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mhead">
          <h2>添加本地 HTML 小工具</h2>
          <button type="button" className="btn ghost sm iconbtn" onClick={onClose} aria-label="关闭">
            <X />
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ margin: 0 }}>
          <div className="mbody">
            <label className="fld">
              <span>工具显示名称</span>
              <input
                ref={nameRef}
                value={name}
                placeholder="例如：JSON 格式化"
                required
                onChange={(e) => setName(e.target.value)}
              />
            </label>

            <label className="fld">
              <span>本地 HTML 文件路径</span>
              <div
                className="row"
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
              >
                <input
                  className="mono"
                  value={file}
                  placeholder="可输入绝对路径，或点右侧浏览选择"
                  required
                  onChange={(e) => setFile(e.target.value)}
                />
                <Button onClick={handlePickFile} disabled={picking} title="打开文件选择器">
                  {picking ? <Loader2 className="spin" /> : <FolderOpen />}
                  <span>浏览...</span>
                </Button>
              </div>
            </label>

            <label className="fld">
              <span>简要说明 (可选)</span>
              <input
                value={desc}
                placeholder="例如：本地离线语法美化"
                onChange={(e) => setDesc(e.target.value)}
              />
            </label>

            {error && (
              <div className="problems">
                <div className="bad">{error}</div>
              </div>
            )}
          </div>

          <div className="mfoot" style={{ padding: '10px 16px 14px' }}>
            <span className="grow" />
            <Button onClick={onClose} disabled={saving}>
              取消
            </Button>
            <Button type="submit" variant="primary" disabled={saving || !name.trim() || !file.trim()}>
              {saving ? '保存中…' : '保存'}
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
