<script setup lang="ts">
import { onMounted } from 'vue'
import { useTodoStore } from '@/stores/todo'

const todo = useTodoStore()
onMounted(() => void todo.fetch())

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
    <h3>TODO priorities</h3>
    <p v-if="todo.error" class="error">{{ todo.error }}</p>
    <p v-if="todo.loading">Loading…</p>
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
              :value="it.priority ?? ''"
              :class="priorityClass(it.priority)"
              @change="changePriority(it.line_no, Number(($event.target as HTMLSelectElement).value))"
            >
              <option v-for="n in 6" :key="n - 1" :value="n - 1">#r{{ n - 1 }}</option>
            </select>
          </td>
          <td>{{ it.title }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else>No TODO entries found.</p>
  </section>
</template>
