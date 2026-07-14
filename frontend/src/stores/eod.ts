import { defineStore } from 'pinia'
import { ref } from 'vue'
import { eodFinalize, eodPublish, getEod } from '@/api/client'
import type { EodEntrySummary, EodPublishResult } from '@/api/types'

export const useEodStore = defineStore('eod', () => {
  const entries = ref<EodEntrySummary[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastPublishResult = ref<EodPublishResult | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      entries.value = await getEod()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function finalize() {
    try {
      await eodFinalize()
      await fetch()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function publish(taskIds: string[]) {
    loading.value = true
    try {
      lastPublishResult.value = await eodPublish(taskIds)
      await fetch()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { entries, loading, error, lastPublishResult, fetch, finalize, publish }
})
