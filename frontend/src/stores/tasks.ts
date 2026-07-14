import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getTaskDetail, getTasksCurrent, getTasksDone } from '@/api/client'
import type { BatchEntryDetail, EodEntrySummary, TaskCurrentResponse } from '@/api/types'

export const useTasksStore = defineStore('tasks', () => {
  const current = ref<TaskCurrentResponse | null>(null)
  const done = ref<EodEntrySummary[]>([])
  const detail = ref<BatchEntryDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchCurrent() {
    try {
      current.value = await getTasksCurrent()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function fetchDone() {
    try {
      done.value = await getTasksDone()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function fetchDetail(taskId: string) {
    loading.value = true
    error.value = null
    try {
      detail.value = await getTaskDetail(taskId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      detail.value = null
    } finally {
      loading.value = false
    }
  }

  async function fetchAll() {
    loading.value = true
    try {
      await Promise.all([fetchCurrent(), fetchDone()])
    } finally {
      loading.value = false
    }
  }

  return { current, done, detail, loading, error, fetchCurrent, fetchDone, fetchDetail, fetchAll }
})
