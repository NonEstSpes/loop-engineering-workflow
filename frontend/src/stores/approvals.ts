import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getApprovals, resolveApproval } from '@/api/client'
import type { ApprovalDecision, ApprovalPending } from '@/api/types'

export const useApprovalsStore = defineStore('approvals', () => {
  const pending = ref<ApprovalPending[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      pending.value = await getApprovals()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function resolve(threadId: string, decision: ApprovalDecision) {
    try {
      await resolveApproval(threadId, decision)
      await fetch() // refresh
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  return { pending, loading, error, fetch, resolve }
})
