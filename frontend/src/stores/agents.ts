import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getAgent, getAgents, saveAgent, updateAgentPrompt } from '@/api/client'
import type { AgentDetail, AgentSummary } from '@/api/types'

export const useAgentsStore = defineStore('agents-control', () => {
  const agents = ref<AgentSummary[]>([])
  const current = ref<AgentDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  // Track which agents have unsaved in-memory prompt changes.
  const modified = ref<Set<string>>(new Set())

  async function fetchList() {
    loading.value = true
    error.value = null
    try {
      agents.value = await getAgents()
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

  async function updatePrompt(name: string, prompt: string) {
    error.value = null
    try {
      await updateAgentPrompt(name, prompt)
      if (current.value && current.value.name === name) {
        current.value.system_prompt = prompt
      }
      modified.value.add(name)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
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

  return { agents, current, loading, error, modified, fetchList, select, updatePrompt, save }
})
