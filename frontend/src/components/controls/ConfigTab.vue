<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useConfigStore } from '@/stores/config'
import CronBuilder from '@/components/controls/CronBuilder.vue'
import type { HitlStrategy } from '@/api/types'

const config = useConfigStore()
const showDiffWarning = ref(false)
onMounted(() => void config.fetch())

const strategies: HitlStrategy[] = ['per_plan', 'full_detail', 'end_of_day']

async function onHitl(strategy: HitlStrategy) {
  try {
    await config.setHitl(strategy)
  } catch {
    // error in store
  }
}

async function onPatchSchedule(field: 'task_schedule' | 'eod_schedule', value: string) {
  try {
    await config.patch({ daemon: { [field]: value } })
  } catch {
    // error in store
  }
}

async function onPatchApproval(field: 'approval_timeout_hours', value: number) {
  try {
    await config.patch({ daemon: { [field]: value } })
  } catch {
    // error in store
  }
}

async function onPatchTimeoutAction(value: string) {
  try {
    await config.patch({ daemon: { approval_on_timeout: value } })
  } catch {
    // error in store
  }
}

async function confirmSave() {
  showDiffWarning.value = false
  try {
    await config.save()
  } catch {
    // error in store
  }
}
</script>

<template>
  <section>
    <h3>Workflow configuration</h3>
    <p v-if="config.error" class="error">{{ config.error }}</p>
    <p v-if="config.loading">Loading…</p>

    <div v-if="config.config" class="card">
      <h4>HITL strategy</h4>
      <div v-for="s in strategies" :key="s">
        <label>
          <input
            type="radio"
            :value="s"
            :checked="config.config.hitl_strategy === s"
            @change="onHitl(s)"
          />
          <code>{{ s }}</code>
        </label>
      </div>
    </div>

    <div v-if="config.config" class="card">
      <h4>Schedules</h4>
      <dl>
        <dt>Task schedule</dt>
        <dd>
          <CronBuilder
            :modelValue="config.config.daemon.task_schedule"
            @update:modelValue="onPatchSchedule('task_schedule', $event)"
            label="Task schedule"
          />
        </dd>
        <dt>EOD schedule</dt>
        <dd>
          <CronBuilder
            :modelValue="config.config.daemon.eod_schedule"
            @update:modelValue="onPatchSchedule('eod_schedule', $event)"
            label="EOD schedule"
          />
        </dd>
      </dl>
    </div>

    <div v-if="config.config" class="card">
      <h4>Approval</h4>
      <dl>
        <dt>Timeout (hours)</dt>
        <dd>
          <input
            type="number"
            :value="config.config.daemon.approval_timeout_hours"
            @change="onPatchApproval('approval_timeout_hours', Number(($event.target as HTMLInputElement).value))"
          />
        </dd>
        <dt>On timeout</dt>
        <dd>
          <select
            :value="config.config.daemon.approval_on_timeout"
            @change="onPatchTimeoutAction(($event.target as HTMLSelectElement).value)"
          >
            <option value="defer">defer</option>
            <option value="reject">reject</option>
          </select>
        </dd>
      </dl>
    </div>

    <div v-if="config.config" class="card">
      <h4>Restart-only fields (read-only)</h4>
      <dl>
        <dt>Port</dt><dd><code>{{ config.config.daemon.port }}</code> (restart only)</dd>
        <dt>Serve frontend</dt><dd><code>{{ config.config.daemon.serve_frontend }}</code> (restart only)</dd>
      </dl>
    </div>

    <div class="actions" v-if="config.diff && !config.diff.clean">
      <span class="unsaved-badge">⚠ Unsaved changes ({{ config.diff.changed.length }})</span>
      <button @click="showDiffWarning = true" :disabled="config.saving">Save to disk</button>
    </div>
    <div v-if="showDiffWarning" class="card">
      <p><strong>Warning:</strong> Saving overwrites <code>workflow.yaml</code> — comments will be lost.</p>
      <ul>
        <li v-for="c in config.diff?.changed" :key="c.field">
          <code>{{ c.field }}</code>: <s>{{ String(c.on_disk) }}</s> → <strong>{{ String(c.in_memory) }}</strong>
        </li>
      </ul>
      <button @click="confirmSave" :disabled="config.saving">Confirm save</button>
      <button @click="showDiffWarning = false">Cancel</button>
    </div>
  </section>
</template>
