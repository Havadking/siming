import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronRight, FolderSearch, Info, Loader2, X } from 'lucide-react'
import { api, type Detected, type Project, type ProjectForm, type Validation } from '../api'
import { Button } from './ui'

const EMPTY: ProjectForm = {
  id: '', name: '', desc: '', cwd: '', cmd: '', port: '', group: '',
  autostart: false, restart: 'never', url: '', url_pattern: '', health: '', env_file: '', env: {},
}

function fromProject(p: Project): ProjectForm {
  return {
    id: p.id, name: p.name, desc: p.desc ?? '', cwd: p.cwd, cmd: p.cmd, port: p.port == null ? '' : String(p.port),
    group: p.group ?? '', autostart: p.autostart, restart: p.restart,
    url: p.url && p.port && p.url === `http://127.0.0.1:${p.port}` ? '' : (p.url ?? ''),
    url_pattern: p.url_pattern ?? '', health: p.health ?? '', env_file: p.env_file ?? '', env: p.env ?? {},
  }
}

const envToText = (env: Record<string, string>) => Object.entries(env).map(([k, v]) => `${k}=${v}`).join('\n')
function textToEnv(t: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const raw of t.split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || !line.includes('=')) continue
    const i = line.indexOf('=')
    const k = line.slice(0, i).trim()
    if (k) out[k] = line.slice(i + 1).trim()
  }
  return out
}

/** 新增 / 编辑项目。editing 非空时 id 只读。 */
export function ProjectDialog({ editing, groups, onClose, onSaved }: {
  editing: Project | null
  groups: string[]
  onClose: () => void
  onSaved: (id: string) => void
}) {
  const [f, setF] = useState<ProjectForm>(() => (editing ? fromProject(editing) : EMPTY))
  const [envText, setEnvText] = useState(() => (editing ? envToText(editing.env ?? {}) : ''))
  const [adv, setAdv] = useState(() => !!editing && !!(f.url || f.url_pattern || f.health || f.env_file || envText))
  const [val, setVal] = useState<Validation | null>(null)
  const [detected, setDetected] = useState<Detected | null>(null)
  const [busy, setBusy] = useState<'detect' | 'pick' | 'save' | null>(null)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const detectedFor = useRef<string | null>(editing?.cwd ?? null)
  const cwdRef = useRef<HTMLInputElement>(null)

  const set = <K extends keyof ProjectForm>(k: K, v: ProjectForm[K]) => setF((s) => ({ ...s, [k]: v }))

  useEffect(() => { cwdRef.current?.focus() }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // 输入停 300ms 干跑校验
  useEffect(() => {
    if (!f.id && !f.cwd && !f.cmd) { setVal(null); return }
    const t = setTimeout(() => {
      api.validate(f, editing?.id ?? null).then(setVal).catch(() => setVal(null))
    }, 300)
    return () => clearTimeout(t)
  }, [f, editing])

  /** 识别目录，只填还是空的字段 */
  const applyDetected = useCallback((d: Detected) => {
    setDetected(d)
    setF((s) => ({
      ...s,
      name: s.name || d.name,
      id: editing ? s.id : (s.id || d.id),
      cmd: s.cmd || d.cmd || '',
      port: s.port || (d.port == null ? '' : String(d.port)),
    }))
  }, [editing])

  const runDetect = useCallback(async (cwd: string) => {
    cwd = cwd.trim()
    if (!cwd || cwd === detectedFor.current) return
    detectedFor.current = cwd
    setBusy('detect')
    try { applyDetected(await api.detect(cwd)) } catch { /* 目录不对，校验那边会说 */ } finally { setBusy(null) }
  }, [applyDetected])

  const pick = async () => {
    setBusy('pick')
    try {
      const r = await api.pickFolder(f.cwd || null)
      if (r.path) {
        set('cwd', r.path)
        detectedFor.current = r.path
        if (r.detected) applyDetected(r.detected)
      }
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(null) }
  }

  const save = async () => {
    setBusy('save'); setSaveErr(null)
    const body = { ...f, env: textToEnv(envText) }
    try {
      const r = editing ? await api.update(editing.id, body) : await api.create(body)
      onSaved(r.id)
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e))
      setBusy(null)
    }
  }

  const hard = val?.hard ?? []
  const soft = val?.soft ?? []
  const canSave = !busy && !!f.cwd && !!f.cmd && !!f.id && hard.length === 0

  return (
    <div className="modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal card" role="dialog" aria-modal aria-labelledby="pd-title">
        <div className="mhead">
          <h2 id="pd-title">{editing ? `编辑：${editing.name}` : '新增项目'}</h2>
          <button type="button" className="btn ghost sm iconbtn" onClick={onClose} aria-label="关闭"><X /></button>
        </div>

        <form className="mbody" onSubmit={(e) => { e.preventDefault(); if (canSave) void save() }}>
          <label className="fld">
            <span>目录</span>
            <div className="row">
              <input ref={cwdRef} className="mono" value={f.cwd} placeholder="E:/personal/projects/xxx" spellCheck={false}
                onChange={(e) => set('cwd', e.target.value)} onBlur={(e) => { void runDetect(e.target.value) }} />
              <Button size="sm" onClick={() => { void pick() }} disabled={busy === 'pick'} title="弹出系统的选择文件夹对话框">
                {busy === 'pick' ? <Loader2 className="spin" /> : <FolderSearch />}选择…
              </Button>
            </div>
          </label>
          {(detected || busy === 'detect') && (
            <div className="detected">
              {busy === 'detect' ? <span className="mute"><Loader2 className="spin" /> 识别中…</span> : detected && (
                <>
                  <span className="mute">{detected.kind === 'node' ? '识别到 package.json' : detected.kind === 'python' ? '识别到 Python 项目' : '没认出项目类型'}</span>
                  {detected.candidates.map((c) => (
                    <button key={c} type="button" className={`chip mono${f.cmd === c ? ' on' : ''}`} onClick={() => set('cmd', c)}>{c}</button>
                  ))}
                  {detected.notes.map((n) => <span key={n} className="note"><Info />{n}</span>)}
                </>
              )}
            </div>
          )}

          <div className="cols">
            <label className="fld"><span>名称</span>
              <input value={f.name} placeholder="显示在卡片上" onChange={(e) => set('name', e.target.value)} />
            </label>
            <label className="fld"><span>id</span>
              <input className="mono" value={f.id} placeholder="a-z 0-9 -" spellCheck={false} readOnly={!!editing}
                title={editing ? 'id 是日志文件名和 URL 的一部分，建了就不改' : undefined}
                onChange={(e) => set('id', e.target.value.toLowerCase())} />
            </label>
          </div>

          <label className="fld"><span>用途</span>
            <input value={f.desc} placeholder="一句话说清它是干嘛的，显示在卡片名字下面" onChange={(e) => set('desc', e.target.value)} />
          </label>

          <label className="fld"><span>命令</span>
            <input className="mono" value={f.cmd} placeholder="npm run dev" spellCheck={false} onChange={(e) => set('cmd', e.target.value)} />
          </label>

          <div className="cols">
            <label className="fld"><span>端口</span>
              <input className="mono" value={f.port} placeholder="可空" inputMode="numeric" onChange={(e) => set('port', e.target.value.replace(/\D/g, ''))} />
            </label>
            <label className="fld"><span>分组</span>
              <input value={f.group} placeholder="可空" list="pd-groups" onChange={(e) => set('group', e.target.value)} />
              <datalist id="pd-groups">{groups.map((g) => <option key={g} value={g} />)}</datalist>
            </label>
          </div>

          <div className="checks">
            <label><input type="checkbox" checked={f.autostart} onChange={(e) => set('autostart', e.target.checked)} />面板启动时跟着起</label>
            <label><input type="checkbox" checked={f.restart === 'on-failure'} onChange={(e) => set('restart', e.target.checked ? 'on-failure' : 'never')} />异常退出后自动重启</label>
          </div>

          <button type="button" className={`advtoggle${adv ? ' open' : ''}`} onClick={() => setAdv((v) => !v)} aria-expanded={adv}>
            <ChevronRight />高级
          </button>
          {adv && (
            <div className="adv">
              <label className="fld"><span>打开链接</span>
                <input className="mono" value={f.url} placeholder={f.port ? `默认 http://127.0.0.1:${f.port}` : '默认由端口生成'} spellCheck={false} onChange={(e) => set('url', e.target.value)} />
              </label>
              <label className="fld"><span>链接正则</span>
                <input className="mono" value={f.url_pattern} placeholder="从 stdout 里捞地址，如 listening on (http\S+)" spellCheck={false} onChange={(e) => set('url_pattern', e.target.value)} />
              </label>
              <label className="fld"><span>健康检查</span>
                <input className="mono" value={f.health} placeholder="/api/health（相对端口）或完整 URL；配了就用它判「在线」，不看端口" spellCheck={false} onChange={(e) => set('health', e.target.value)} />
              </label>
              <label className="fld"><span>env 文件</span>
                <input className="mono" value={f.env_file} placeholder=".env（相对目录；放密码用）" spellCheck={false} onChange={(e) => set('env_file', e.target.value)} />
              </label>
              <label className="fld"><span>环境变量</span>
                <textarea className="mono" rows={3} value={envText} placeholder={'KEY=VALUE\n一行一个；别放密码，这文件在 git 里'} spellCheck={false} onChange={(e) => setEnvText(e.target.value)} />
              </label>
            </div>
          )}

          {(hard.length > 0 || soft.length > 0 || saveErr) && (
            <div className="problems">
              {saveErr && <div className="bad"><AlertTriangle />{saveErr}</div>}
              {hard.map((m) => <div key={m} className="bad"><AlertTriangle />{m}</div>)}
              {soft.map((m) => <div key={m} className="warn"><AlertTriangle />{m}<span className="mute">（可以先保存，卡片上会标配置错误）</span></div>)}
            </div>
          )}

          <div className="mfoot">
            <span className="mute small">保存直接写进 projects.yaml，注释和顺序会保留。</span>
            <span className="grow" />
            <Button onClick={onClose}>取消</Button>
            <Button type="submit" variant="primary" disabled={!canSave}>{busy === 'save' ? '保存中…' : editing ? '保存' : '添加'}</Button>
          </div>
        </form>
      </div>
    </div>
  )
}
