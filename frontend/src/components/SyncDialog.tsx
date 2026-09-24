import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, GitMerge, Loader2, RefreshCw, Terminal, X } from 'lucide-react'
import { api, type Incoming, type MergeResult, type Project } from '../api'
import { ago } from '../lib/format'
import { Button } from './ui'

const LIVE = new Set(['running', 'starting', 'unhealthy', 'external', 'restarting'])

function readPref(key: string, dflt: boolean): boolean {
  try { const v = localStorage.getItem(key); return v == null ? dflt : v === '1' } catch { return dflt }
}
function writePref(key: string, v: boolean) {
  try { localStorage.setItem(key, v ? '1' : '0') } catch { /* ignore */ }
}

const short = (ref: string) => ref.replace(/^origin\//, '')

const STALE_AFTER = 3 * 86400
/** 最近提交超过 3 天的云端分支：多半是放弃了的旧会话，默认不勾，免得一合就冲突 */
const isStale = (i: Incoming) => i.kind === 'cloud' && i.at != null && Date.now() / 1000 - i.at > STALE_AFTER

/**
 * 把 GitHub 上还没合进本地的分支一键合进来（DESIGN.md 11）。
 * 打开时先 fetch 一次；列表来自 p.git.incoming（轮询会带回最新的）。手动勾过的记在 picked 里，
 * 没动过的按默认：新分支勾上，旧分支（isStale）不勾。
 */
export function SyncDialog({ p, onClose, onChanged, onRestart }: {
  p: Project
  onClose: () => void
  onChanged: () => void          // 刷新项目列表
  onRestart: () => void
}) {
  const g = p.git
  const incoming = g?.incoming ?? []
  const live = LIVE.has(p.status) && p.status !== 'external'
  const [busy, setBusy] = useState<'fetch' | 'merge' | null>(null)
  const [fetchErr, setFetchErr] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [result, setResult] = useState<MergeResult | null>(null)
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  const [push, setPush] = useState(() => readPref('sync-push', false))
  const [restart, setRestart] = useState(() => readPref('sync-restart', true))

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, busy])

  const doFetch = useCallback(async () => {
    setBusy('fetch'); setFetchErr(null)
    try {
      const r = await api.gitFetch(p.id)
      setFetchErr(r.error)
      onChanged()
    } catch (e) {
      setFetchErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }, [p.id, onChanged])

  const fetchedOnOpen = useRef(false)
  useEffect(() => {
    if (fetchedOnOpen.current) return
    fetchedOnOpen.current = true
    void doFetch()
  }, [doFetch])

  const isOn = (i: Incoming) => picked[i.ref] ?? !isStale(i)
  const chosen = incoming.filter(isOn)
  const merge = async () => {
    setBusy('merge'); setErr(null); setResult(null)
    try {
      const r = await api.gitMerge(p.id, chosen.map((i) => i.ref), push)
      setResult(r)
      if (r.merged.length && restart && live) onRestart()
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const ignore = async (ref: string, sha: string) => {
    try { await api.gitIgnore(p.id, ref, sha); onChanged() } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
  }

  const toggle = (i: Incoming) => setPicked((m) => ({ ...m, [i.ref]: !isOn(i) }))

  const fetchedLine = busy === 'fetch'
    ? <span className="mute"><Loader2 className="spin" /> 正在从 GitHub 拉取…</span>
    : fetchErr || g?.fetch_error
      ? <span className="bad" title={fetchErr ?? g?.fetch_error ?? ''}><AlertTriangle />拉取失败：{fetchErr ?? g?.fetch_error}</span>
      : g?.fetched_at ? <span className="mute">{ago(g.fetched_at)}拉取过</span> : null

  return (
    <div className="modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <div className="modal card sync" role="dialog" aria-modal aria-labelledby="sd-title">
        <div className="mhead">
          <h2 id="sd-title">同步云端改动 · {p.name}</h2>
          <button type="button" className="btn ghost sm iconbtn" onClick={() => { void doFetch() }} disabled={!!busy}
            aria-label="重新拉取" title="重新从 GitHub 拉取"><RefreshCw className={busy === 'fetch' ? 'spin' : undefined} /></button>
          <button type="button" className="btn ghost sm iconbtn" onClick={onClose} disabled={!!busy} aria-label="关闭"><X /></button>
        </div>

        <div className="mbody">
          <div className="sync-meta mono">
            <span>当前分支 <b>{g?.branch ?? '?'}</b></span>
            {g && g.ahead > 0 && <span className="mute">本地领先 {g.upstream ?? '上游'} {g.ahead} 个</span>}
            <span className="grow" />
            {fetchedLine}
          </div>

          {g && g.dirty > 0 && (
            <div className="problems"><div className="warn"><AlertTriangle />
              <span>有 {g.dirty} 处未提交（含未跟踪文件）。已跟踪的文件有改动时合并会被拒绝，先提交或 stash。</span></div></div>
          )}

          {incoming.length === 0
            ? (
              <div className="sync-empty">
                {busy === 'fetch' ? <Loader2 className="spin" /> : <CheckCircle2 />}
                {busy === 'fetch' ? '看看 GitHub 上有什么新的…' : '没有待合并的改动，本地已经是最新的。'}
              </div>
            )
            : (
              <ul className="incoming-list">
                {incoming.map((i) => (
                  <li key={i.ref} className={isOn(i) ? undefined : 'off'}>
                    <label className="ihead">
                      <input type="checkbox" checked={isOn(i)} onChange={() => toggle(i)} disabled={!!busy} />
                      <span className={`pill ${i.kind === 'cloud' ? 'accent' : 'info'}`}>{i.kind === 'cloud' ? '云端' : '上游'}</span>
                      {isStale(i) && <span className="pill warn" title="最近提交超过 3 天，多半是不要了的旧会话，默认不勾">旧</span>}
                      <span className="ref mono" title={i.ref}>{short(i.ref)}</span>
                      <span className="mute">{i.count} 个提交{i.at ? ` · ${ago(i.at)}` : ''}</span>
                      {i.kind === 'cloud' && i.behind_head > 0 && (
                        <span className={i.behind_head >= 10 ? 'warn-text' : 'mute'} title={`本地有 ${i.behind_head} 个提交它没有；越多越可能冲突`}>
                          · 落后本地 {i.behind_head}
                        </span>
                      )}
                      <span className="grow" />
                      {i.kind === 'cloud' && (
                        <button type="button" className="linkbtn" disabled={!!busy} onClick={(e) => { e.preventDefault(); void ignore(i.ref, i.sha) }}
                          title="不再提示这个分支；它以后有新提交会重新出现">忽略</button>
                      )}
                    </label>
                    <ul className="subjects">
                      {i.subjects.map((s, k) => <li key={k}>{s}</li>)}
                      {i.count > i.subjects.length && <li className="mute">…还有 {i.count - i.subjects.length} 个</li>}
                    </ul>
                  </li>
                ))}
              </ul>
            )}

          {err && <div className="problems"><div className="bad"><AlertTriangle /><span>{err}</span></div></div>}
          {result && (
            <div className="problems">
              {result.merged.length > 0 && (
                <div className="ok"><CheckCircle2 /><span>
                  已合并 {result.merged.map(short).join('、')}
                  {result.pushed && '，已推送到 origin'}
                  {restart && live && '，项目正在重启'}
                </span></div>
              )}
              {result.push_error && <div className="warn"><AlertTriangle /><span>合并好了但推送失败：{result.push_error}</span></div>}
              {result.failed.map((f) => (
                <div key={f.ref} className="bad"><AlertTriangle /><span>
                  {short(f.ref)}：{f.message}
                  {f.conflicts.length > 0 && <> —— 冲突文件 <span className="mono">{f.conflicts.join('、')}</span>。本地没有半截合并；要它就去终端手动合并或让 Claude 处理，不要就点「忽略」。</>}
                  {f.conflicts.length > 0 && (
                    <button type="button" className="linkbtn" onClick={() => { void api.openTerminal(p.id) }}><Terminal />在终端打开</button>
                  )}
                </span></div>
              ))}
              {result.merged.length === 0 && result.failed.length === 0 && <div className="ok"><CheckCircle2 /><span>都已经在本地了，没什么要合的。</span></div>}
            </div>
          )}

          <div className="checks">
            <label title="合并后 git push 当前分支，GitHub 上的主分支也跟上">
              <input type="checkbox" checked={push} disabled={!!busy} onChange={(e) => { setPush(e.target.checked); writePref('sync-push', e.target.checked) }} />
              合并后推送到 origin
            </label>
            <label title={live ? '代码变了，重启一下项目才生效' : '项目没在跑'}>
              <input type="checkbox" checked={restart && live} disabled={!!busy || !live} onChange={(e) => { setRestart(e.target.checked); writePref('sync-restart', e.target.checked) }} />
              合并后重启项目
            </label>
          </div>

          <div className="mfoot">
            <span className="mute sync-hint">能快进就快进，否则生成一个合并提交；某个分支冲突就撤销它，接着合别的。</span>
            <span className="grow" />
            <Button size="sm" onClick={onClose} disabled={!!busy}>关闭</Button>
            <Button size="sm" variant="primary" disabled={!!busy || chosen.length === 0} onClick={() => { void merge() }}>
              {busy === 'merge' ? <Loader2 className="spin" /> : <GitMerge />}
              {chosen.length > 1 ? `合并 ${chosen.length} 个分支` : '合并到本地'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

