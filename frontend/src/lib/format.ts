/** 秒 → "2h 13m" / "4s"。starting 时用秒级。 */
export function duration(sec: number | null, opts?: { seconds?: boolean }): string {
  if (sec == null || !isFinite(sec)) return '—'
  const s = Math.max(0, Math.floor(sec))
  if (opts?.seconds || s < 60) return `${s}s`
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

export function bytes(n: number | null): string {
  if (n == null) return '—'
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  if (n < 1024 * 1024 * 1024) return `${Math.round(n / 1024 / 1024)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** 过去的时间点 → "3 分钟前" */
export function ago(ts: number | null): string {
  if (ts == null) return ''
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  return `${Math.floor(s / 86400)} 天前`
}
