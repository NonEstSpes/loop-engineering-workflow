<script setup lang="ts">
import { ref, computed } from 'vue'
import { useEodStore } from '@/stores/eod'
import { usePolling } from '@/composables/usePolling'

const store = useEodStore()
const { refresh } = usePolling(() => store.fetch(), 10000)
void refresh()

const selected = ref<Set<string>>(new Set())

function toggle(taskId: string) {
  if (selected.value.has(taskId)) {
    selected.value.delete(taskId)
  } else {
    selected.value.add(taskId)
  }
  // trigger reactivity
  selected.value = new Set(selected.value)
}

const selectedList = computed(() => Array.from(selected.value))

async function publishSelected() {
  await store.publish(selectedList.value)
  selected.value = new Set()
}

async function publishAll() {
  await store.publish([]) // empty = all
  selected.value = new Set()
}
</script>

<template>
  <section>
    <h2>EOD Review</h2>
    <p v-if="store.error" class="error">Error: {{ store.error }}</p>

    <div class="actions">
      <button @click="store.finalize()">Finalize (refresh pending)</button>
      <button :disabled="!selectedList.length" @click="publishSelected">
        Publish selected ({{ selectedList.length }})
      </button>
      <button :disabled="!store.entries.length" @click="publishAll">
        Publish ALL
      </button>
    </div>

    <div v-if="store.lastPublishResult" class="card">
      <h3>Last publish result</h3>
      <p>Published: {{ store.lastPublishResult.published.join(', ') || 'none' }}</p>
      <p>Failed: {{ store.lastPublishResult.failed.join(', ') || 'none' }}</p>
      <p>Skipped: {{ store.lastPublishResult.skipped.join(', ') || 'none' }}</p>
    </div>

    <p v-if="!store.entries.length">No pending EOD entries.</p>

    <table v-else>
      <thead>
        <tr>
          <th></th>
          <th>Task</th><th>Title</th><th>Branch</th><th>Verdict</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="e in store.entries" :key="e.id">
          <td>
            <input
              type="checkbox"
              :checked="selected.has(e.task_id)"
              @change="toggle(e.task_id)"
            />
          </td>
          <td>
            <RouterLink :to="`/tasks/${e.task_id}`">{{ e.task_id }}</RouterLink>
          </td>
          <td>{{ e.task_title }}</td>
          <td><code>{{ e.branch_name }}</code></td>
          <td>{{ e.final_verdict ?? '–' }}</td>
          <td>{{ e.status }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
