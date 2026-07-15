import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getTodo, patchTodo } from '@/api/client'
import type { TodoItem, TodoPatch } from '@/api/types'

export const useTodoStore = defineStore('todo-control', () => {
  const items = ref<TodoItem[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      items.value = await getTodo()
      lastUpdated.value = new Date()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function updateLine(lineNo: number, patch: TodoPatch) {
    error.value = null
    try {
      const updated = await patchTodo(lineNo, patch)
      const idx = items.value.findIndex((it) => it.line_no === lineNo)
      if (idx >= 0) items.value[idx] = updated
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  return { items, loading, error, lastUpdated, fetch, updateLine }
})
