import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getEventHistory } from '@/api/client'
import type { EventLogEntry } from '@/api/types'

export const useActivityStore = defineStore('activity', () => {
  const events = ref<EventLogEntry[]>([])
  const filter = ref<string>('') // '' = all
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      events.value = await getEventHistory(100, filter.value || undefined)
      lastUpdated.value = new Date()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  function setFilter(f: string) {
    filter.value = f
    void fetch()
  }

  /** Prepend a new event (for live SSE updates). */
  function prepend(entry: EventLogEntry) {
    if (!events.value.some((e) => e.id === entry.id)) {
      events.value.unshift(entry)
    }
  }

  return { events, filter, loading, error, lastUpdated, fetch, setFilter, prepend }
})
