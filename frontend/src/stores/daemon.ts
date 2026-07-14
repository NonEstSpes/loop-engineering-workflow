import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getHealth, getState } from '@/api/client'
import type { HealthResponse, StateResponse } from '@/api/types'

export const useDaemonStore = defineStore('daemon', () => {
  const health = ref<HealthResponse | null>(null)
  const state = ref<StateResponse | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchAll() {
    loading.value = true
    error.value = null
    try {
      const [h, s] = await Promise.all([getHealth(), getState()])
      health.value = h
      state.value = s
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { health, state, loading, error, fetchAll }
})
