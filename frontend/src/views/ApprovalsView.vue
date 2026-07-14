<script setup lang="ts">
import { ref } from 'vue'
import { useApprovalsStore } from '@/stores/approvals'
import { usePolling } from '@/composables/usePolling'

const store = useApprovalsStore()
const { refresh } = usePolling(() => store.fetch(), 4000)
void refresh()

// Per-approval form state (keyed by thread_id).
const reasons = ref<Record<string, string>>({})
const changes = ref<Record<string, string>>({})

async function approve(threadId: string) {
  await store.resolve(threadId, {
    approved: true,
    reason: reasons.value[threadId] ?? '',
  })
  delete reasons.value[threadId]
}

async function reject(threadId: string) {
  await store.resolve(threadId, {
    approved: false,
    reason: reasons.value[threadId] ?? '',
    requested_changes: (changes.value[threadId] ?? '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean),
  })
  delete reasons.value[threadId]
  delete changes.value[threadId]
}
</script>

<template>
  <section>
    <h2>Approvals</h2>
    <p v-if="store.error" class="error">Error: {{ store.error }}</p>
    <p v-if="!store.pending.length">No pending approvals.</p>

    <div v-for="a in store.pending" :key="a.thread_id" class="card">
      <h3>{{ (a.payload as any).task_title ?? a.thread_id }}</h3>
      <p><strong>Gate:</strong> {{ (a.payload as any).gate_type ?? 'unknown' }}</p>
      <p><strong>Task:</strong> {{ (a.payload as any).task_id ?? '–' }}</p>
      <details v-if="(a.payload as any).plan_summary">
        <summary>Plan</summary>
        <pre>{{ (a.payload as any).plan_summary }}</pre>
      </details>
      <details v-if="(a.payload as any).diff">
        <summary>Diff</summary>
        <pre>{{ (a.payload as any).diff }}</pre>
      </details>

      <textarea
        v-model="reasons[a.thread_id]"
        placeholder="Reason (optional)"
        rows="2"
        style="width: 100%"
      ></textarea>
      <textarea
        v-model="changes[a.thread_id]"
        placeholder="Requested changes (one per line, for reject)"
        rows="3"
        style="width: 100%"
      ></textarea>
      <button @click="approve(a.thread_id)">Approve</button>
      <button @click="reject(a.thread_id)">Reject</button>
    </div>
  </section>
</template>
