import { onBeforeUnmount, onMounted, type Ref } from 'vue'
import { ref } from 'vue'

/**
 * Periodically call `fn` every `intervalMs` while the component is mounted.
 * Returns an active ref and manual refresh control.
 */
export function usePolling(fn: () => Promise<void>, intervalMs: number) {
  const active = ref(true)
  let timer: number | null = null

  async function tick() {
    try {
      await fn()
    } catch {
      // swallow — the store sets its own error ref
    }
  }

  function start() {
    if (timer !== null) return
    active.value = true
    tick()
    timer = window.setInterval(tick, intervalMs)
  }

  function stop() {
    active.value = false
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  onMounted(start)
  onBeforeUnmount(stop)

  return { active: active as Ref<boolean>, refresh: tick, stop }
}
