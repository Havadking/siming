export interface Sample { t: number; v: number }

/** 断档阈值：轮询 2s 一次，页面在后台时会停；超过这个间隔就断线不连 */
const GAP = 6

/**
 * 迷你折线。56×16，`preserveAspectRatio="none"` 随容器拉伸。
 * - floor=true：y 从 0 起算（CPU）
 * - 否则 min–max 缩放，但最小量程是 max 的 minRange 倍（内存：别把 1MB 抖动画成山峰）
 */
export function Sparkline({ data, floor = false, minRange = 0.05, className, title }: {
  data: Sample[]
  floor?: boolean
  minRange?: number
  className?: string
  title?: string
}) {
  if (data.length < 2) return null
  const W = 56, H = 16, PAD = 1.5
  const t0 = data[0].t
  const t1 = data[data.length - 1].t
  const span = Math.max(t1 - t0, 1)
  let lo = floor ? 0 : Infinity
  let hi = -Infinity
  for (const s of data) { if (s.v < lo) lo = s.v; if (s.v > hi) hi = s.v }
  if (floor) hi = Math.max(hi, 1)
  else {
    const min = Math.max(hi * minRange, 1e-9)
    if (hi - lo < min) { const mid = (hi + lo) / 2; lo = Math.max(0, mid - min / 2); hi = lo + min }
  }
  const range = hi - lo || 1
  const x = (t: number) => ((t - t0) / span) * W
  const y = (v: number) => H - PAD - ((v - lo) / range) * (H - 2 * PAD)
  let d = ''
  for (let i = 0; i < data.length; i++) {
    const s = data[i]
    const brk = i === 0 || s.t - data[i - 1].t > GAP
    d += `${brk ? 'M' : 'L'}${x(s.t).toFixed(1)},${y(s.v).toFixed(1)}`
  }
  return (
    <svg className={`spark${className ? ` ${className}` : ''}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden>
      {title && <title>{title}</title>}
      <path d={d} fill="none" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}
