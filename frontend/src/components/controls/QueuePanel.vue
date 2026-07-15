<script setup lang="ts">
import { ref } from 'vue'
import { useQueueStore } from '@/stores/queue'
import { usePolling } from '@/composables/usePolling'

const queue = useQueueStore()
const draggedTaskId = ref<string | null>(null)
const dragOverTaskId = ref<string | null>(null)

const { refresh } = usePolling(() => queue.fetch(), 30000)

function onDragStart(taskId: string) {
  draggedTaskId.value = taskId
}

function onDragOver(taskId: string, e: DragEvent) {
  e.preventDefault()
  dragOverTaskId.value = taskId
}

function onDrop(targetTaskId: string, e: DragEvent) {
  e.preventDefault()
  if (draggedTaskId.value && draggedTaskId.value !== targetTaskId) {
    const targetIdx = queue.queue.findIndex((q) => q.task_id === targetTaskId)
    if (targetIdx >= 0) {
      void queue.reorder(draggedTaskId.value, targetIdx)
    }
  }
  draggedTaskId.value = null
  dragOverTaskId.value = null
}

function priorityClass(p: number | null): string {
  if (p === 0) return 'prio-critical'
  if (p === 1) return 'prio-urgent'
  return 'prio-normal'
}
</script>

<template>
  <section class="queue-panel">
    <div class="tasks-header">
      <h3>Execution queue</h3>
      <div class="refresh-section">
        <button @click="refresh()" :disabled="queue.loading" class="refresh-btn">
          ↻ {{ queue.loading ? 'Загрузка…' : 'Обновить' }}
        </button>
        <small v-if="queue.lastUpdated" class="last-updated">
          Обновлено: {{ queue.lastUpdated.toLocaleTimeString() }}
        </small>
      </div>
    </div>
    <p v-if="queue.error" class="error">{{ queue.error }}</p>
    <p v-if="!queue.queue.length && !queue.loading">Очередь пуста. Запустите задачу — LLM оценит порядок.</p>
    <ul v-if="queue.queue.length" class="queue-list">
      <li
        v-for="entry in queue.queue"
        :key="entry.task_id"
        class="queue-item"
        :class="{ 'drag-over': dragOverTaskId === entry.task_id }"
        draggable="true"
        @dragstart="onDragStart(entry.task_id)"
        @dragover="onDragOver(entry.task_id, $event)"
        @drop="onDrop(entry.task_id, $event)"
        @dragend="draggedTaskId = null; dragOverTaskId = null"
      >
        <span class="drag-handle" title="Перетащите для изменения порядка">⠿</span>
        <span class="queue-position">{{ entry.position + 1 }}.</span>
        <code>#{{ entry.task_id }}</code>
        <span class="queue-title">{{ entry.task_title }}</span>
        <span v-if="entry.priority !== null" :class="priorityClass(entry.priority)">[r{{ entry.priority }}]</span>
        <span class="queue-actions">
          <button
            @click="queue.moveUp(entry.task_id)"
            :disabled="entry.position === 0"
            class="move-btn"
            title="Вверх"
          >↑</button>
          <button
            @click="queue.moveDown(entry.task_id)"
            :disabled="entry.position === queue.queue.length - 1"
            class="move-btn"
            title="Вниз"
          >↓</button>
        </span>
      </li>
    </ul>
    <small class="dnd-hint">Перетащите ⠿ для изменения порядка</small>
  </section>
</template>
