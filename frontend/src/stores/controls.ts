import { defineStore } from 'pinia'
import { ref } from 'vue'
import { runTask } from '@/api/client'
import type { RunResponse } from '@/api/types'

interface RunHistoryEntry {
  run_id: string
  task_id: string | null
  started_at: number
  status: 'started' | 'finished' | 'error'
}

export const useControlsStore = defineStore('controls', () => {
  const isRunning = ref(false)
  const currentRun = ref<RunResponse | null>(null)
  const runHistory = ref<RunHistoryEntry[]>([])
  const error = ref<string | null>(null)

  async function run(taskId?: string) {
    error.value = null
    try {
      const resp = await runTask({ task_id: taskId })
      currentRun.value = resp
      isRunning.value = true
      runHistory.value.unshift({
        run_id: resp.run_id,
        task_id: resp.task_id,
        started_at: Date.now(),
        status: 'started',
      })
      // Keep only last 5.
      if (runHistory.value.length > 5) {
        runHistory.value = runHistory.value.slice(0, 5)
      }
      return resp
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  function markFinished(_runId: string, status: 'finished' | 'error') {
    const entry = runHistory.value.find((r) => r.status === 'started')
    if (entry) entry.status = status
    isRunning.value = false
    currentRun.value = null
  }

  function clearError() {
    error.value = null
  }

  return { isRunning, currentRun, runHistory, error, run, markFinished, clearError }
})
