import { useCallback, useEffect, useRef, useState } from 'react'

const MAX_LINES = 2000

/** EventSource 封装：id 为空时不连；换 id 清空重连；返回行数组和「是否连接中」。 */
export function useLogStream(url: string | null) {
  const [lines, setLines] = useState<string[]>([])
  const [connected, setConnected] = useState(false)
  const pending = useRef<string[]>([])
  const flushTimer = useRef<number | null>(null)

  useEffect(() => {
    setLines([])
    pending.current = []
    if (!url) { setConnected(false); return }
    const es = new EventSource(url)
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.onmessage = (ev) => {
      // 回放 200 行会瞬间来一堆事件，攒一帧再 setState
      pending.current.push(ev.data as string)
      if (flushTimer.current == null) {
        flushTimer.current = requestAnimationFrame(() => {
          flushTimer.current = null
          const batch = pending.current
          pending.current = []
          setLines((prev) => {
            const next = prev.concat(batch)
            return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next
          })
        })
      }
    }
    return () => {
      es.close()
      if (flushTimer.current != null) cancelAnimationFrame(flushTimer.current)
      flushTimer.current = null
      setConnected(false)
    }
  }, [url])

  const clear = useCallback(() => setLines([]), [])
  return { lines, connected, clear }
}
