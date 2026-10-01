import { useEffect, useRef } from 'react'

/** Call `callback` now, then every intervalMs while the tab is visible; refresh
 * as soon as it becomes visible again. Restarts when deps change. */
export function useAutoRefresh(callback: () => void, intervalMs: number, deps: unknown[]) {
  const saved = useRef(callback)
  saved.current = callback

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined
    const start = () => {
      stop()
      timer = setInterval(() => saved.current(), intervalMs)
    }
    const stop = () => {
      if (timer !== undefined) clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        saved.current()
        start()
      } else {
        stop()
      }
    }
    saved.current()
    if (document.visibilityState === 'visible') start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restart only on the caller's deps
  }, [intervalMs, ...deps])
}
