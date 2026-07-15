<script setup lang="ts">
import { useTodoStore } from '@/stores/todo'
import { usePolling } from '@/composables/usePolling'

const todo = useTodoStore()
// Auto-refresh every 30s (list changes rarely; button + SSE cover the rest).
const { refresh } = usePolling(() => todo.fetch(), 30000)

// Initial fetch is handled by usePolling's onMounted(start).

function priorityClass(p: number | null): string {
  if (p === 0) return 'prio-critical'
  if (p === 1) return 'prio-urgent'
  return 'prio-normal'
}

async function changePriority(lineNo: number, priority: number) {
  try {
    await todo.updateLine(lineNo, { priority })
  } catch {
    // error in store
  }
}

async function toggleStatus(lineNo: number, current: string) {
  const next = current === '[ ]' ? 'in_progress' : current === '[~]' ? 'done' : 'open'
  try {
    await todo.updateLine(lineNo, { status: next })
  } catch {
    // error in store
  }
}
</script>

<template>
  <section>
    <div class="tasks-header">
      <h3>TASKS</h3>
      <div class="refresh-section">
        <button @click="refresh()" :disabled="todo.loading" class="refresh-btn">
          ↻ {{ todo.loading ? 'Загрузка…' : 'Обновить' }}
        </button>
        <small v-if="todo.lastUpdated" class="last-updated">
          Обновлено: {{ todo.lastUpdated.toLocaleTimeString() }}
        </small>
      </div>
    </div>
    <p v-if="todo.error" class="error">{{ todo.error }}</p>
    <table v-if="todo.items.some(i => i.checkbox !== null)">
      <thead>
        <tr><th>Line</th><th>Status</th><th>Priority</th><th>Title</th></tr>
      </thead>
      <tbody>
        <tr v-for="it in todo.items.filter(i => i.checkbox !== null)" :key="it.line_no">
          <td><code>{{ it.line_no }}</code></td>
          <td>
            <button class="checkbox-btn" @click="toggleStatus(it.line_no, it.checkbox!)">
              {{ it.checkbox }}
            </button>
          </td>
          <td>
            <select
              v-if="it.priority !== null"
              :value="it.priority"
              :class="priorityClass(it.priority)"
              @change="changePriority(it.line_no, Number(($event.target as HTMLSelectElement).value))"
            >
              <option v-for="n in 6" :key="n - 1" :value="n - 1">#r{{ n - 1 }}</option>
            </select>
            <span v-else class="prio-none">—</span>
          </td>
          <td>{{ it.title }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else>Нет задач.</p>
  </section>
</template>
