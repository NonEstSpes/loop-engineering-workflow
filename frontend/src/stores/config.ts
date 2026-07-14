import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getConfig, getConfigDiff, patchConfig, saveConfig, switchHitl } from '@/api/client'
import type { ConfigDiff, ConfigPatch, ConfigResponse, HitlStrategy } from '@/api/types'

export const useConfigStore = defineStore('config-control', () => {
  const config = ref<ConfigResponse | null>(null)
  const diff = ref<ConfigDiff | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const saving = ref(false)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      const [cfg, d] = await Promise.all([getConfig(), getConfigDiff()])
      config.value = cfg
      diff.value = d
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function patch(p: ConfigPatch) {
    error.value = null
    try {
      config.value = await patchConfig(p)
      diff.value = await getConfigDiff()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  async function setHitl(strategy: HitlStrategy) {
    error.value = null
    try {
      await switchHitl(strategy)
      if (config.value) config.value.hitl_strategy = strategy
      diff.value = await getConfigDiff()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  async function save() {
    saving.value = true
    error.value = null
    try {
      await saveConfig()
      diff.value = await getConfigDiff()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  return { config, diff, loading, error, saving, fetch, patch, setHitl, save }
})
