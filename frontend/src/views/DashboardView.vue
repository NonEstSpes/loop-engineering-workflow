<script setup lang="ts">
import { useDaemonStore } from '@/stores/daemon'
import { useTasksStore } from '@/stores/tasks'
import { useControlsStore } from '@/stores/controls'
import { usePolling } from '@/composables/usePolling'

const daemon = useDaemonStore()
const tasks = useTasksStore()
const controls = useControlsStore()

const { refresh } = usePolling(async () => {
  await Promise.all([daemon.fetchAll(), tasks.fetchAll()])
}, 5000)

// initial fetch
void refresh()

// Quick "Run next" from the dashboard: reuse the controls store.
// run() with no task id runs the next task by priority.
async function onRunNext() {
  try {
    await controls.run()
    await refresh()
  } catch {
    // error already surfaced in controls.error
  }
}
</script>

<template>
  <section>
    <h2>Dashboard</h2>
    <p v-if="daemon.error" class="error">Error: {{ daemon.error }}</p>

    <div v-if="daemon.health" class="card">
      <h3>Health</h3>
      <dl>
        <dt>Status</dt><dd>{{ daemon.health.status }}</dd>
        <dt>Scheduler</dt><dd>{{ daemon.health.scheduler }}</dd>
        <dt>Uptime</dt><dd>{{ daemon.health.uptime_seconds }}s</dd>
        <dt>Pending approvals</dt><dd>{{ daemon.health.pending_approvals }}</dd>
        <dt>Batch store pending</dt><dd>{{ daemon.health.batch_store_pending }}</dd>
      </dl>
    </div>

    <div v-if="daemon.state" class="card">
      <h3>Config</h3>
      <dl>
        <dt>HITL strategy</dt><dd>{{ daemon.state.hitl_strategy }}</dd>
        <dt>Task source</dt><dd>{{ daemon.state.task_source }}</dd>
        <dt>Task schedule</dt><dd><code>{{ daemon.state.daemon.task_schedule }}</code></dd>
        <dt>EOD schedule</dt><dd><code>{{ daemon.state.daemon.eod_schedule }}</code></dd>
      </dl>
    </div>

    <div class="card">
      <h3>Current task</h3>
      <p v-if="tasks.current?.task_id">
        Active: <strong>{{ tasks.current.task_id }}</strong>
        <span v-if="tasks.current.node"> (node: {{ tasks.current.node }})</span>
      </p>
      <p v-else>No task currently running.</p>
      <p v-if="controls.error" class="error">{{ controls.error }}</p>
      <button
        class="run-btn"
        @click="onRunNext"
        :disabled="controls.isRunning || !!tasks.current?.task_id"
        :title="tasks.current?.task_id ? 'A task is already running' : 'Run the next task by priority'"
      >
        {{ controls.isRunning ? 'Starting…' : '▶ Run next' }}
      </button>
    </div>

    <div class="card">
      <h3>Done today ({{ tasks.done.length }})</h3>
      <ul v-if="tasks.done.length">
        <li v-for="t in tasks.done" :key="t.id">
          <RouterLink :to="`/tasks/${t.task_id}`">{{ t.task_id }}</RouterLink>
          — {{ t.task_title }} ({{ t.final_verdict ?? '–' }})
        </li>
      </ul>
      <p v-else>No completed tasks.</p>
    </div>
  </section>
</template>
