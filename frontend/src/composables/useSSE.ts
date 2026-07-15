import { onBeforeUnmount, onMounted } from 'vue'
import { useDaemonStore } from '@/stores/daemon'
import { useTasksStore } from '@/stores/tasks'
import { useEodStore } from '@/stores/eod'
import { useControlsStore } from '@/stores/controls'
import { useTodoStore } from '@/stores/todo'
import { useQueueStore } from '@/stores/queue'

/**
 * Connect to /api/events SSE stream and refresh relevant stores on events.
 * Reconnects on error with a 5s backoff.
 */
export function useSSE() {
  const daemon = useDaemonStore()
  const tasks = useTasksStore()
  const eod = useEodStore()
  const controls = useControlsStore()
  const todo = useTodoStore()
  const queue = useQueueStore()

  let source: EventSource | null = null
  let reconnectTimer: number | null = null

  function connect() {
    source = new EventSource('/api/events')

    source.addEventListener('task.started', () => {
      void tasks.fetchCurrent()
    })
    source.addEventListener('task.finished', () => {
      void tasks.fetchCurrent()
      void tasks.fetchDone()
      controls.markFinished('', 'finished')
    })
    source.addEventListener('task.error', () => {
      void tasks.fetchCurrent()
      void daemon.fetchAll()
      controls.markFinished('', 'error')
    })
    source.addEventListener('eod.ready', () => {
      void eod.fetch()
    })
    source.addEventListener('tasks.updated', () => {
      void todo.fetch()
    })
    source.addEventListener('queue.updated', () => {
      void queue.fetch()
    })
    // NOTE: 'approval.waiting' is not yet emitted by the backend (ApprovalBridge
    // does not publish to EventBus on register). Approvals are poll-only
    // (ApprovalsView polls every 4s) until that emission is added.

    source.onerror = () => {
      source?.close()
      source = null
      // Reconnect after 5s.
      reconnectTimer = window.setTimeout(connect, 5000)
    }
  }

  onMounted(connect)
  onBeforeUnmount(() => {
    source?.close()
    source = null
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  })
}
