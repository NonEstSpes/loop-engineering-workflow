<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: string
  label: string
}>()
const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

type RepeatMode = 'daily' | 'weekdays' | 'weekend' | 'specificDays' | 'everyN'
const repeatMode = ref<RepeatMode>('weekdays')
const selectedDays = ref<number[]>([1, 2, 3, 4, 5]) // 0=Sun..6=Sat
const timeEntries = ref<string[]>(['09:00'])
const everyNValue = ref(30)
const everyNUnit = ref<'minutes' | 'hours'>('minutes')
const showHelp = ref(false)

const dayNames = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']

// Parse a cron string into builder fields (best-effort).
function parseCron(cron: string): void {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return
  const [min, hour, , , dow] = parts
  // Every N minutes/hours: */N * * * *
  if (min.startsWith('*/') && hour === '*' && dow === '*') {
    repeatMode.value = 'everyN'
    everyNValue.value = parseInt(min.slice(2), 10)
    everyNUnit.value = 'minutes'
    return
  }
  // Every N hours: 0 */N * * *
  if (hour.startsWith('*/') && min === '0' && dow === '*') {
    repeatMode.value = 'everyN'
    everyNValue.value = parseInt(hour.slice(2), 10)
    everyNUnit.value = 'hours'
    return
  }
  // Parse times: min + hour → multiple HH:MM
  const mins = min.includes(',') ? min.split(',') : [min]
  const hours = hour.includes(',') ? hour.split(',') : [hour]
  const times: string[] = []
  for (const h of hours) {
    for (const m of mins) {
      times.push(`${h.padStart(2, '0')}:${m.padStart(2, '0')}`)
    }
  }
  timeEntries.value = times.sort()
  // Determine repeat mode from DOW
  if (dow === '*') {
    repeatMode.value = 'daily'
  } else if (dow === '1-5') {
    repeatMode.value = 'weekdays'
  } else if (dow === '6,0' || dow === '0,6') {
    repeatMode.value = 'weekend'
  } else {
    repeatMode.value = 'specificDays'
    selectedDays.value = dow.split(',').map((d) => parseInt(d, 10)).filter((n) => !isNaN(n))
  }
}

// Build cron from fields
const cronString = computed<string>(() => {
  if (repeatMode.value === 'everyN') {
    if (everyNUnit.value === 'minutes') {
      return `*/${everyNValue.value} * * * *`
    }
    return `0 */${everyNValue.value} * * *`
  }
  // Parse time entries into mins/hours
  const mins = [...new Set(timeEntries.value.map((t) => t.split(':')[1]))].sort()
  const hours = [...new Set(timeEntries.value.map((t) => t.split(':')[0]))].sort()
  const minPart = mins.join(',')
  const hourPart = hours.join(',')
  let dow = '*'
  if (repeatMode.value === 'daily') dow = '*'
  else if (repeatMode.value === 'weekdays') dow = '1-5'
  else if (repeatMode.value === 'weekend') dow = '6,0'
  else if (repeatMode.value === 'specificDays') {
    dow = selectedDays.value.length ? selectedDays.value.sort().join(',') : '*'
  }
  return `${minPart} ${hourPart} * * ${dow}`
})

// Human-readable preview
const preview = computed<string>(() => {
  const times = timeEntries.value.join(', ')
  if (repeatMode.value === 'everyN') {
    return `Каждые ${everyNValue.value} ${everyNUnit.value === 'minutes' ? 'минут' : 'часов'}`
  }
  const dayText: Record<RepeatMode, string> = {
    daily: 'Ежедневно',
    weekdays: 'По будням',
    weekend: 'По выходным',
    specificDays: `В дни: ${selectedDays.value.map((d) => dayNames[d]).join(', ')}`,
    everyN: '',
  }
  return `${dayText[repeatMode.value]} в ${times}`
})

watch(cronString, (val) => emit('update:modelValue', val))
watch(() => props.modelValue, (val) => parseCron(val), { immediate: true })

function addTime() {
  timeEntries.value.push('12:00')
}
function removeTime(idx: number) {
  if (timeEntries.value.length > 1) {
    timeEntries.value.splice(idx, 1)
  }
}
</script>

<template>
  <div class="cron-builder">
    <label class="cron-label">{{ label }}</label>

    <fieldset>
      <legend>Повторение</legend>
      <label><input type="radio" v-model="repeatMode" value="daily" /> Ежедневно</label>
      <label><input type="radio" v-model="repeatMode" value="weekdays" /> По будням (пн–пт)</label>
      <label><input type="radio" v-model="repeatMode" value="weekend" /> По выходным (сб–вс)</label>
      <label><input type="radio" v-model="repeatMode" value="specificDays" /> Конкретные дни</label>
      <div v-if="repeatMode === 'specificDays'" class="day-checkboxes">
        <label v-for="(day, idx) in dayNames" :key="idx">
          <input type="checkbox" :value="idx" v-model="selectedDays" /> {{ day }}
        </label>
      </div>
      <label><input type="radio" v-model="repeatMode" value="everyN" /> Каждые</label>
      <span v-if="repeatMode === 'everyN'" class="every-n">
        <input type="number" v-model.number="everyNValue" min="1" />
        <select v-model="everyNUnit">
          <option value="minutes">минут</option>
          <option value="hours">часов</option>
        </select>
      </span>
    </fieldset>

    <fieldset v-if="repeatMode !== 'everyN'">
      <legend>Время</legend>
      <div v-for="(t, idx) in timeEntries" :key="idx" class="time-entry">
        <input type="time" v-model="timeEntries[idx]" />
        <button @click="removeTime(idx)" v-if="timeEntries.length > 1" class="remove-time">×</button>
      </div>
      <button @click="addTime" class="add-time">+ Добавить время</button>
    </fieldset>

    <div class="cron-preview">
      <strong>Preview:</strong> {{ preview }}
      <br />
      <small>Raw: <code>{{ cronString }}</code></small>
    </div>

    <button @click="showHelp = !showHelp" class="help-toggle">
      {{ showHelp ? '▾ Скрыть справку' : '▸ Справка по формату cron' }}
    </button>
    <div v-if="showHelp" class="cron-help">
      <p><strong>Формат cron</strong> — 5 полей, разделённых пробелами:</p>
      <ul>
        <li><code>минута</code> (0–59)</li>
        <li><code>час</code> (0–23)</li>
        <li><code>день месяца</code> (1–31)</li>
        <li><code>месяц</code> (1–12 или JAN–DEC)</li>
        <li><code>день недели</code> (0–6, где 0=воскресенье, или SUN–SAT)</li>
      </ul>
      <p><strong>Спецсимволы:</strong></p>
      <ul>
        <li><code>*</code> — любое значение</li>
        <li><code>,</code> — список значений (например <code>1,15</code>)</li>
        <li><code>-</code> — диапазон (например <code>1-5</code>)</li>
        <li><code>/</code> — шаг (например <code>*/15</code> = каждые 15 единиц)</li>
      </ul>
      <p><strong>Alias:</strong></p>
      <ul>
        <li><code>@daily</code> = <code>0 0 * * *</code> (каждый день в полночь)</li>
        <li><code>@hourly</code> = <code>0 * * * *</code> (каждый час)</li>
        <li><code>@weekly</code> = <code>0 0 * * 0</code> (каждое воскресенье)</li>
        <li><code>@monthly</code> = <code>0 0 1 * *</code> (1-го числа каждого месяца)</li>
      </ul>
      <p><strong>Пример:</strong> <code>0 9,15 * * 1-5</code> = по будням в 09:00 и 15:00.</p>
    </div>
  </div>
</template>
