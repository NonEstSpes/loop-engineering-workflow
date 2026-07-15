import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getQueue, queueMoveDown, queueMoveUp, reorderQueue } from '@/api/client'
import type { QueueEntry } from '@/api/types'

export const useQueueStore = defineStore('queue', () => {
  const queue = ref<QueueEntry[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      queue.value = await getQueue()
      lastUpdated.value = new Date()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function reorder(taskId: string, newPosition: number) {
    error.value = null
    try {
      queue.value = await reorderQueue({ task_id: taskId, new_position: newPosition })
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function moveUp(taskId: string) {
    error.value = null
    try {
      queue.value = await queueMoveUp(taskId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function moveDown(taskId: string) {
    error.value = null
    try {
      queue.value = await queueMoveDown(taskId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  return { queue, loading, error, lastUpdated, fetch, reorder, moveUp, moveDown }
})
