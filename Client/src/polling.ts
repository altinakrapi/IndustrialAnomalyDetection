export function startVisiblePolling(
  poll: (signal: AbortSignal) => Promise<void>,
  intervalMs: number,
): () => void {
  let active = true
  let inFlight = false
  let refreshWhenVisible = false
  let timer: number | undefined
  let controller: AbortController | null = null

  const run = async () => {
    if (!active || inFlight || document.visibilityState !== 'visible') return
    inFlight = true
    const requestController = new AbortController()
    controller = requestController
    try {
      await poll(requestController.signal)
    } finally {
      if (controller === requestController) controller = null
      inFlight = false
      if (active && document.visibilityState === 'visible') {
        if (refreshWhenVisible) {
          refreshWhenVisible = false
          void run()
        } else {
          timer = window.setTimeout(() => void run(), intervalMs)
        }
      }
    }
  }

  const handleVisibilityChange = () => {
    if (document.visibilityState === 'hidden') {
      if (timer !== undefined) window.clearTimeout(timer)
      timer = undefined
      controller?.abort()
      return
    }
    refreshWhenVisible = true
    if (!inFlight) {
      refreshWhenVisible = false
      void run()
    }
  }

  document.addEventListener('visibilitychange', handleVisibilityChange)
  void run()

  return () => {
    active = false
    if (timer !== undefined) window.clearTimeout(timer)
    controller?.abort()
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  }
}
