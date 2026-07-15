<script setup lang="ts">
import { ref } from 'vue'
import { useControlsStore } from '@/stores/controls'

const controls = useControlsStore()
const taskId = ref('')

async function onRun() {
  try {
    await controls.run(taskId.value || undefined)
  } catch {
    // error already in store
  }
}
</script>

<template>
  <section>
    <h3>Run a task</h3>
    <p v-if="controls.error" class="error">{{ controls.error }}</p>
    <div class="actions">
      <input
        v-model="taskId"
        placeholder="Task ID (empty = run next by priority)"
        :disabled="controls.isRunning"
      />
      <button @click="onRun" :disabled="controls.isRunning">
        {{ taskId ? 'Run task' : 'Run next' }}
      </button>
    </div>
    <p v-if="controls.isRunning" class="running-badge">
      ⏳ Running: <strong>{{ controls.currentRun?.task_id ?? 'next by priority' }}</strong>
    </p>
    <p v-else class="idle-badge">✓ Idle</p>

    <h4>Recent runs</h4>
    <ul v-if="controls.runHistory.length">
      <li v-for="r in controls.runHistory" :key="r.run_id">
        <code>{{ r.task_id ?? 'next' }}</code> —
        <span :class="r.status">{{ r.status }}</span>
        <small> ({{ new Date(r.started_at).toLocaleTimeString() }})</small>
      </li>
    </ul>
    <p v-else>No runs yet this session.</p>
  </section>
</template>
