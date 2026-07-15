<script setup lang="ts">
import { useActivityStore } from '@/stores/activity'
import { usePolling } from '@/composables/usePolling'

const activity = useActivityStore()
const { refresh } = usePolling(() => activity.fetch(), 30000)

const filterOptions = [
  { value: '', label: 'Все события' },
  { value: 'task', label: 'Задачи' },
  { value: 'approval', label: 'Approvals' },
  { value: 'queue', label: 'Очередь' },
]

function typeClass(eventType: string): string {
  if (eventType.startsWith('task')) return 'event-task'
  if (eventType.startsWith('approval')) return 'event-approval'
  if (eventType.startsWith('queue')) return 'event-queue'
  if (eventType.includes('error')) return 'event-error'
  return 'event-default'
}

function formatTime(timestamp: string): string {
  const d = new Date(timestamp)
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<template>
  <section>
    <div class="tasks-header">
      <h2>Activity Log</h2>
      <div class="refresh-section">
        <select :value="activity.filter" @change="activity.setFilter(($event.target as HTMLSelectElement).value)" class="activity-filter">
          <option v-for="opt in filterOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <button @click="refresh()" :disabled="activity.loading" class="refresh-btn">
          ↻ {{ activity.loading ? 'Загрузка…' : 'Обновить' }}
        </button>
        <small v-if="activity.lastUpdated" class="last-updated">
          Обновлено: {{ activity.lastUpdated.toLocaleTimeString() }}
        </small>
      </div>
    </div>
    <p v-if="activity.error" class="error">{{ activity.error }}</p>
    <ul v-if="activity.events.length" class="activity-list">
      <li v-for="ev in activity.events" :key="ev.id" class="activity-item">
        <span class="activity-time">{{ formatTime(ev.timestamp) }}</span>
        <code :class="typeClass(ev.event_type)">{{ ev.event_type }}</code>
        <span class="activity-data">{{ JSON.stringify(ev.data).slice(0, 120) }}</span>
      </li>
    </ul>
    <div v-else-if="!activity.loading" class="empty-state">
      <p class="empty-icon">📭</p>
      <p>Нет событий в журнале.</p>
      <p class="empty-hint">События появятся при работе workflow (запуск задач, approvals, оценка очереди).</p>
    </div>
  </section>
</template>
