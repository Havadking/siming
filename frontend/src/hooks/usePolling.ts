import { useCallback, useEffect, useRef, useState } from 'react'

/** 每 interval 毫秒调一次 fn；页面在后台时暂停。refresh() 立刻拉一次。 */
export function usePolling<T>(fn: () => Promise<T>, interval: number) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const inflight = useRef(false)

  const refresh = useCallback(async () => {
    if (inflight.current) return
    inflight.current = true
    try {
      setData(await fnRef.current())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      inflight.current = false
    }
  }, [])

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null
    const start = () => {
      if (timer) return
      void refresh()
      timer = setInterval(() => { void refresh() }, interval)
    }
    const stop = () => { if (timer) { clearInterval(timer); timer = null } }
    const onVis = () => (document.hidden ? stop() : start())
    start()
    document.addEventListener('visibilitychange', onVis)
    return () => { stop(); document.removeEventListener('visibilitychange', onVis) }
  }, [interval, refresh])

  return { data, error, refresh }
}
