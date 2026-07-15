import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getAgent, getAgents, getProviders, saveAgent, updateAgent, updateAgentPrompt } from '@/api/client'
import type { AgentDetail, AgentSummary, ProviderSummary } from '@/api/types'

export const useAgentsStore = defineStore('agents-control', () => {
  const agents = ref<AgentSummary[]>([])
  const providers = ref<ProviderSummary[]>([])
  const current = ref<AgentDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  // Track which agents have unsaved in-memory prompt changes.
  const modified = ref<Set<string>>(new Set())

  async function fetchList() {
    loading.value = true
    error.value = null
    try {
      const [a, p] = await Promise.all([getAgents(), getProviders()])
      agents.value = a
      providers.value = p
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function select(name: string) {
    error.value = null
    try {
      current.value = await getAgent(name)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function updateFields(name: string, update: { system_prompt?: string; provider?: string; model?: string; temperature?: number }) {
    error.value = null
    try {
      await updateAgent(name, update)
      if (current.value && current.value.name === name) {
        if (update.system_prompt !== undefined) current.value.system_prompt = update.system_prompt
        if (update.provider !== undefined) current.value.provider = update.provider
        if (update.model !== undefined) current.value.model = update.model
        if (update.temperature !== undefined) current.value.temperature = update.temperature
      }
      modified.value.add(name)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  // Keep updatePrompt as alias for the textarea debounced handler.
  async function updatePrompt(name: string, prompt: string) {
    return updateFields(name, { system_prompt: prompt })
  }

  async function save(name: string) {
    error.value = null
    try {
      await saveAgent(name)
      modified.value.delete(name)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  return { agents, providers, current, loading, error, modified, fetchList, select, updateFields, updatePrompt, save }
})
